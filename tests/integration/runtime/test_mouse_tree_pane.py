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
    return getattr(callbacks, name)

class AppRuntimeMouseTestsPart1(unittest.TestCase):
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
