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

class AppRuntimeMouseTestsPart2(unittest.TestCase):
    def test_source_mouse_drag_copies_selected_text_to_clipboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text("alpha beta\nsecond line\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

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
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 6}:1")
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 4}:2")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 4}:2")
                snapshots["anchor"] = state.source_selection_anchor
                snapshots["focus"] = state.source_selection_focus

            def fake_which(cmd: str) -> str | None:
                if cmd == "pbcopy":
                    return "/usr/bin/pbcopy"
                return None

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app_helpers.sys.platform", "darwin"
            ), mock.patch(
                "lazyviewer.runtime.app_helpers.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.runtime.app_helpers.subprocess.run") as subprocess_run, mock.patch(
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
                app_runtime.run_pager("", file_path, "monokai", True, False)

            clipboard_calls = [
                call
                for call in subprocess_run.call_args_list
                if call.args and call.args[0] == ["pbcopy"]
            ]
            self.assertEqual(len(clipboard_calls), 1)
            self.assertEqual(
                clipboard_calls[0].kwargs,
                {
                    "input": "beta\nseco",
                    "text": True,
                    "check": False,
                },
            )
            self.assertEqual(snapshots["anchor"], (0, 6))
            self.assertEqual(snapshots["focus"], (1, 4))

    def test_source_mouse_drag_autoscrolls_when_pointer_moves_below_view(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text(
                "".join(f"line {idx:03d}\n" for idx in range(1, 181)),
                encoding="utf-8",
            )
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                tick_source_selection_drag = _callback(kwargs, "tick_source_selection_drag")
                right_start_col = state.left_width + 2
                snapshots["start_before"] = state.start
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 2}:1")
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 2}:999")
                snapshots["start_after_drag"] = state.start
                for _ in range(5):
                    tick_source_selection_drag()
                snapshots["start_after_wait"] = state.start
                snapshots["focus_after_drag"] = state.source_selection_focus
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 2}:999")

            def fake_which(cmd: str) -> str | None:
                if cmd == "pbcopy":
                    return "/usr/bin/pbcopy"
                return None

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app_helpers.sys.platform", "darwin"
            ), mock.patch(
                "lazyviewer.runtime.app_helpers.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.runtime.app_helpers.subprocess.run"), mock.patch(
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
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertIn("start_before", snapshots)
            self.assertIn("start_after_drag", snapshots)
            self.assertIn("start_after_wait", snapshots)
            self.assertGreater(int(snapshots["start_after_drag"]), int(snapshots["start_before"]))
            self.assertGreater(int(snapshots["start_after_wait"]), int(snapshots["start_after_drag"]))
            focus = snapshots["focus_after_drag"]
            self.assertIsNotNone(focus)
            assert isinstance(focus, tuple)
            self.assertGreaterEqual(focus[0], int(snapshots["start_after_drag"]))

    def test_source_mouse_drag_autoscrolls_when_pointer_holds_at_top(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text(
                "".join(f"line {idx:03d}\n" for idx in range(1, 241)),
                encoding="utf-8",
            )
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                tick_source_selection_drag = _callback(kwargs, "tick_source_selection_drag")
                right_start_col = state.left_width + 2
                state.start = min(state.max_start, 80)
                snapshots["start_before"] = state.start
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 2}:4")
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 2}:1")
                snapshots["start_after_drag"] = state.start
                for _ in range(5):
                    tick_source_selection_drag()
                snapshots["start_after_wait"] = state.start
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 2}:1")

            def fake_which(cmd: str) -> str | None:
                if cmd == "pbcopy":
                    return "/usr/bin/pbcopy"
                return None

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app_helpers.sys.platform", "darwin"
            ), mock.patch(
                "lazyviewer.runtime.app_helpers.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.runtime.app_helpers.subprocess.run"), mock.patch(
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
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertIn("start_before", snapshots)
            self.assertIn("start_after_drag", snapshots)
            self.assertIn("start_after_wait", snapshots)
            self.assertLess(int(snapshots["start_after_drag"]), int(snapshots["start_before"]))
            self.assertLess(int(snapshots["start_after_wait"]), int(snapshots["start_after_drag"]))
