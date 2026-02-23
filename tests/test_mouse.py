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
from lazyviewer.ansi import ANSI_ESCAPE_RE
from lazyviewer.runtime.screen import (
    _centered_scroll_start,
    _first_git_change_screen_line,
    _tree_order_key_for_relative_path,
)
from lazyviewer.git_status import GIT_STATUS_CHANGED
from lazyviewer.navigation import JumpLocation
from lazyviewer.render import help_panel_row_count, render_dual_page
from lazyviewer.search.content import ContentMatch


def _callback(kwargs: dict[str, object], name: str):
    callbacks = kwargs["callbacks"]
    return getattr(callbacks, name)


class AppRuntimeMouseTests(unittest.TestCase):
    def test_double_click_file_copies_file_name_to_clipboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text("print('x')\n", encoding="utf-8")

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                target_idx = next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.path.resolve() == file_path.resolve()
                )
                row = (target_idx - state.tree_start) + 1
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:1:{row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:1:{row}")

            def fake_which(cmd: str) -> str | None:
                if cmd == "pbcopy":
                    return "/usr/bin/pbcopy"
                return None

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.runtime.app.subprocess.run") as subprocess_run, mock.patch(
                "lazyviewer.runtime.app.time.monotonic", return_value=100.0
            ), mock.patch("lazyviewer.runtime.app.os.isatty", return_value=True), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            clipboard_calls = [
                call
                for call in subprocess_run.call_args_list
                if call.args and call.args[0] == ["pbcopy"]
            ]
            self.assertEqual(len(clipboard_calls), 1)
            self.assertEqual(
                clipboard_calls[0].kwargs,
                {
                    "input": "demo.py",
                    "text": True,
                    "check": False,
                },
            )

    def test_single_click_directory_arrow_toggles_open_and_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            docs_dir = root / "docs"
            docs_dir.mkdir()
            nested_file = docs_dir / "guide.md"
            nested_file.write_text("# guide\n", encoding="utf-8")
            snapshots: dict[str, bool] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")

                def find_docs_index() -> int:
                    return next(
                        idx
                        for idx, entry in enumerate(state.tree_entries)
                        if entry.path.resolve() == docs_dir.resolve()
                    )

                docs_idx = find_docs_index()
                docs_entry = state.tree_entries[docs_idx]
                docs_row = (docs_idx - state.tree_start) + 1
                arrow_col = 1 + (docs_entry.depth * 2)
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{arrow_col}:{docs_row}")

                snapshots["opened"] = docs_dir.resolve() in state.expanded
                snapshots["child_visible_after_open"] = any(
                    entry.path.resolve() == nested_file.resolve() for entry in state.tree_entries
                )

                docs_idx = find_docs_index()
                docs_entry = state.tree_entries[docs_idx]
                docs_row = (docs_idx - state.tree_start) + 1
                arrow_col = 1 + (docs_entry.depth * 2)
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{arrow_col}:{docs_row}")

                snapshots["closed"] = docs_dir.resolve() not in state.expanded
                snapshots["child_hidden_after_close"] = not any(
                    entry.path.resolve() == nested_file.resolve() for entry in state.tree_entries
                )

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertTrue(snapshots["opened"])
            self.assertTrue(snapshots["child_visible_after_open"])
            self.assertTrue(snapshots["closed"])
            self.assertTrue(snapshots["child_hidden_after_close"])

    def test_single_click_directory_name_does_not_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            docs_dir = root / "docs"
            docs_dir.mkdir()
            (docs_dir / "guide.md").write_text("# guide\n", encoding="utf-8")
            snapshots: dict[str, bool] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                docs_idx = next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.path.resolve() == docs_dir.resolve()
                )
                docs_entry = state.tree_entries[docs_idx]
                docs_row = (docs_idx - state.tree_start) + 1
                # Click name area, not the arrow marker.
                name_col = (1 + (docs_entry.depth * 2)) + 2
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{name_col}:{docs_row}")
                snapshots["still_collapsed"] = docs_dir.resolve() not in state.expanded

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertTrue(snapshots["still_collapsed"])

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
                "lazyviewer.runtime.app.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.runtime.app.subprocess.run") as subprocess_run, mock.patch(
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

            self.assertIn("start_before", snapshots)
            self.assertIn("start_after_drag", snapshots)
            self.assertIn("start_after_wait", snapshots)
            self.assertLess(int(snapshots["start_after_drag"]), int(snapshots["start_before"]))
            self.assertLess(int(snapshots["start_after_wait"]), int(snapshots["start_after_drag"]))

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

