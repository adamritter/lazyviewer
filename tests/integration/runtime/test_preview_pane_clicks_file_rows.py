"""Integration-heavy tests for ``lazyviewer.runtime.app`` wiring.

Covers git/watch refresh behavior, key-driven state transitions, and search flows.
These tests ensure runtime callbacks and state orchestration stay coherent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from lazyviewer.runtime import app as app_runtime
from lazyviewer.render.ansi import ANSI_ESCAPE_RE
from lazyviewer.runtime.screen import (
    _centered_scroll_start,
    _first_git_change_screen_line,
    _tree_order_key_for_relative_path,
)
from lazyviewer.git_status import GIT_STATUS_CHANGED
from lazyviewer.runtime.navigation import JumpLocation
from lazyviewer.render import help_panel_row_count, render_dual_page
from lazyviewer.search.content import ContentMatch


def _callback(kwargs: dict[str, object], name: str):
    callbacks = kwargs["callbacks"]
    if hasattr(callbacks, name):
        return getattr(callbacks, name)

    tree_pane = getattr(callbacks, "tree_pane", None)
    source_pane = getattr(callbacks, "source_pane", None)
    layout = getattr(callbacks, "layout", None)
    if tree_pane is None or source_pane is None or layout is None:
        raise AttributeError(f"{type(callbacks).__name__} has no callback {name!r}")

    mapping = {
        "activate_tree_filter_selection": tree_pane.filter.activate_tree_filter_selection,
        "apply_tree_filter_query": tree_pane.filter.apply_tree_filter_query,
        "close_tree_filter": tree_pane.filter.close_tree_filter,
        "handle_normal_key": callbacks.handle_normal_key,
        "handle_tree_mouse_click": tree_pane.handle_tree_mouse_click,
        "handle_tree_mouse_wheel": source_pane.handle_tree_mouse_wheel,
        "maybe_refresh_git_watch": callbacks.maybe_refresh_git_watch,
        "open_tree_filter": tree_pane.filter.open_tree_filter,
        "rebuild_screen_lines": layout.rebuild_screen_lines,
        "refresh_git_status_overlay": callbacks.refresh_git_status_overlay,
        "save_left_pane_width": callbacks.save_left_pane_width,
        "set_named_mark": tree_pane.navigation.set_named_mark,
        "tick_source_selection_drag": getattr(callbacks, "tick_source_selection_drag", lambda: None),
    }
    if name not in mapping:
        raise AttributeError(f"{type(callbacks).__name__} has no callback {name!r}")
    return mapping[name]

class AppRuntimePreviewClickTestsPart2(unittest.TestCase):
    def test_clicking_file_name_in_preview_selects_it_in_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target_file = root / "README.md"
            target_file.write_text("hello\n", encoding="utf-8")
            (root / "other.txt").write_text("x\n", encoding="utf-8")
            snapshots: dict[str, Path] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                right_start_col = state.left_width + 2

                target_row = None
                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line)
                    if "README.md" in plain:
                        target_row = idx + 1
                        break
                self.assertIsNotNone(target_row)
                assert target_row is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 3}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 3}:{target_row}")

                snapshots["current_path"] = state.current_path.resolve()
                snapshots["selected_path"] = state.tree_entries[state.selected_idx].path.resolve()

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertEqual(snapshots["current_path"], target_file.resolve())
            self.assertEqual(snapshots["selected_path"], target_file.resolve())

    def test_clicking_nested_file_name_in_preview_keeps_full_nested_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp) / "lazyviewer").resolve()
            root.mkdir()
            nested_dir = root / "lazyviewer"
            nested_dir.mkdir()
            target_file = nested_dir / "app_runtime.py"
            target_file.write_text("print('nested')\n", encoding="utf-8")
            # Also create same leaf name at root to catch parent-level mis-resolution.
            (root / "app_runtime.py").write_text("print('root')\n", encoding="utf-8")
            snapshots: dict[str, Path] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                right_start_col = state.left_width + 2

                target_row = None
                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line)
                    if "└─ app_runtime.py" in plain or "├─ app_runtime.py" in plain:
                        if "lazyviewer" in plain:
                            # This line is likely the nested one in narrow panes.
                            pass
                    if "app_runtime.py" in plain and ("│  " in plain or "   " in plain):
                        target_row = idx + 1
                        break
                self.assertIsNotNone(target_row)
                assert target_row is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 3}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 3}:{target_row}")

                snapshots["current_path"] = state.current_path.resolve()
                snapshots["selected_path"] = state.tree_entries[state.selected_idx].path.resolve()

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertEqual(snapshots["current_path"], target_file.resolve())
            self.assertEqual(snapshots["selected_path"], target_file.resolve())

    def test_clicking_nested_file_name_in_preview_with_git_badge_keeps_full_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp) / "lazyviewer").resolve()
            root.mkdir()
            nested_dir = root / "lazyviewer"
            nested_dir.mkdir()
            target_file = nested_dir / "app_runtime.py"
            target_file.write_text("print('nested')\n", encoding="utf-8")
            snapshots: dict[str, Path] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                refresh_git_status_overlay = _callback(kwargs, "refresh_git_status_overlay")
                right_start_col = state.left_width + 2

                refresh_git_status_overlay(force=True)

                target_row = None
                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line)
                    if "app_runtime.py" in plain and "[M]" in plain and ("│  " in plain or "   " in plain):
                        target_row = idx + 1
                        break
                self.assertIsNotNone(target_row)
                assert target_row is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 3}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 3}:{target_row}")

                snapshots["current_path"] = state.current_path.resolve()
                snapshots["selected_path"] = state.tree_entries[state.selected_idx].path.resolve()

            overlay = {target_file.resolve(): GIT_STATUS_CHANGED, nested_dir.resolve(): GIT_STATUS_CHANGED}
            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.collect_git_status_overlay", return_value=overlay
            ), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertEqual(snapshots["current_path"], target_file.resolve())
            self.assertEqual(snapshots["selected_path"], target_file.resolve())
