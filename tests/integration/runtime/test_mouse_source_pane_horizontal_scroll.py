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
        "tick_source_selection_drag": tree_pane.tick_source_selection_drag,
    }
    if name not in mapping:
        raise AttributeError(f"{type(callbacks).__name__} has no callback {name!r}")
    return mapping[name]

class AppRuntimeMouseTestsPart3(unittest.TestCase):
    def test_source_mouse_drag_autoscrolls_horizontally_when_pointer_holds_at_right_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "wide.py"
            file_path.write_text(
                "".join(f"line_{idx:03d} = {'x' * 260}\n" for idx in range(1, 12)),
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
                right_edge_col = right_start_col + state.right_width - 1
                row = 2

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 2}:{row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_edge_col}:{row}")
                snapshots["text_x_after_drag"] = state.text_x
                snapshots["focus_after_drag"] = state.source_selection_focus
                for _ in range(6):
                    tick_source_selection_drag()
                snapshots["text_x_after_wait"] = state.text_x
                snapshots["focus_after_wait"] = state.source_selection_focus
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_edge_col}:{row}")

            def fake_which(cmd: str) -> str | None:
                if cmd == "pbcopy":
                    return "/usr/bin/pbcopy"
                return None

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.runtime.app.subprocess.run"), mock.patch(
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

            self.assertIn("text_x_after_drag", snapshots)
            self.assertIn("text_x_after_wait", snapshots)
            self.assertGreater(int(snapshots["text_x_after_wait"]), int(snapshots["text_x_after_drag"]))
            self.assertIsNotNone(snapshots["focus_after_drag"])
            self.assertIsNotNone(snapshots["focus_after_wait"])
            focus_after_drag = snapshots["focus_after_drag"]
            focus_after_wait = snapshots["focus_after_wait"]
            assert isinstance(focus_after_drag, tuple)
            assert isinstance(focus_after_wait, tuple)
            self.assertGreater(focus_after_wait[1], focus_after_drag[1])

    def test_mouse_wheel_horizontal_scrolls_preview_and_clamps_to_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "wide.txt"
            file_path.write_text(("x" * 220) + "\nshort\n", encoding="utf-8")
            snapshots: dict[str, int | bool] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_wheel = _callback(kwargs, "handle_tree_mouse_wheel")
                right_col = state.left_width + 2
                row = 1

                snapshots["expected_max"] = max(0, 220 - state.right_width)
                snapshots["initial_text_x"] = state.text_x
                snapshots["handled_first"] = handle_tree_mouse_wheel(f"MOUSE_WHEEL_RIGHT:{right_col}:{row}")
                snapshots["after_first"] = state.text_x
                for _ in range(300):
                    handle_tree_mouse_wheel(f"MOUSE_WHEEL_RIGHT:{right_col}:{row}")
                snapshots["after_many"] = state.text_x
                snapshots["handled_at_max"] = handle_tree_mouse_wheel(f"MOUSE_WHEEL_RIGHT:{right_col}:{row}")
                snapshots["after_extra"] = state.text_x
                handle_tree_mouse_wheel(f"MOUSE_WHEEL_LEFT:{right_col}:{row}")
                snapshots["after_left"] = state.text_x

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
                app_runtime.run_pager("", file_path, "monokai", True, False)

            expected_max = int(snapshots["expected_max"])
            self.assertEqual(snapshots["initial_text_x"], 0)
            self.assertTrue(bool(snapshots["handled_first"]))
            self.assertTrue(bool(snapshots["handled_at_max"]))
            self.assertEqual(snapshots["after_many"], expected_max)
            self.assertEqual(snapshots["after_extra"], expected_max)
            if expected_max > 0:
                self.assertGreater(int(snapshots["after_first"]), 0)
                self.assertLess(int(snapshots["after_left"]), int(snapshots["after_extra"]))
            else:
                self.assertEqual(snapshots["after_first"], 0)
                self.assertEqual(snapshots["after_left"], 0)

    def test_mouse_wheel_horizontal_does_not_scroll_when_content_fits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "short.txt"
            file_path.write_text("short line\nok\n", encoding="utf-8")
            snapshots: dict[str, int | bool] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_wheel = _callback(kwargs, "handle_tree_mouse_wheel")
                right_col = state.left_width + 2
                row = 1
                snapshots["before"] = state.text_x
                snapshots["handled"] = handle_tree_mouse_wheel(f"MOUSE_WHEEL_RIGHT:{right_col}:{row}")
                snapshots["after"] = state.text_x

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
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertTrue(bool(snapshots["handled"]))
            self.assertEqual(snapshots["before"], 0)
            self.assertEqual(snapshots["after"], 0)
