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


class AppRuntimeContentSearchTests(unittest.TestCase):
    def test_content_search_preview_selection_jumps_to_hit_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text("line\n" * 120, encoding="utf-8")
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, _query, _show_hidden, **_kwargs):
                return (
                    {
                        file_path.resolve(): [
                            ContentMatch(
                                path=file_path.resolve(),
                                line=80,
                                column=1,
                                preview="line",
                            )
                        ]
                    },
                    False,
                    None,
                )

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                open_tree_filter = _callback(kwargs, "open_tree_filter")
                apply_tree_filter_query = _callback(kwargs, "apply_tree_filter_query")
                open_tree_filter("content")
                apply_tree_filter_query("line", preview_selection=True, select_first_file=True)
                snapshots["current_path"] = state.current_path.resolve()
                snapshots["start"] = state.start
                snapshots["selected_kind"] = state.tree_entries[state.selected_idx].kind if state.tree_entries else ""

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.filter_panel.controller.search_project_content_rg", side_effect=fake_search_content
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

            self.assertEqual(snapshots["current_path"], file_path.resolve())
            self.assertEqual(snapshots["selected_kind"], "search_hit")
            self.assertGreater(int(snapshots["start"]), 0)

    def test_content_search_typing_and_enter_keep_origin_until_hit_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            current_file = root / "zzz.py"
            other_file = root / "aaa.py"
            current_file.write_text("line\n" * 120, encoding="utf-8")
            other_file.write_text("line\n" * 120, encoding="utf-8")
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, _query, _show_hidden, **_kwargs):
                return (
                    {
                        other_file.resolve(): [
                            ContentMatch(
                                path=other_file.resolve(),
                                line=10,
                                column=1,
                                preview="needle",
                            )
                        ],
                        current_file.resolve(): [
                            ContentMatch(
                                path=current_file.resolve(),
                                line=80,
                                column=1,
                                preview="needle",
                            )
                        ],
                    },
                    False,
                    None,
                )

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                open_tree_filter = _callback(kwargs, "open_tree_filter")
                apply_tree_filter_query = _callback(kwargs, "apply_tree_filter_query")
                activate_tree_filter_selection = _callback(kwargs, "activate_tree_filter_selection")
                state.start = 30
                state.text_x = 4
                snapshots["origin_path"] = state.current_path.resolve()
                snapshots["origin_start"] = state.start
                snapshots["origin_text_x"] = state.text_x

                open_tree_filter("content")
                apply_tree_filter_query("needle", preview_selection=False, select_first_file=False)
                snapshots["after_typing_path"] = state.current_path.resolve()
                snapshots["after_typing_start"] = state.start
                snapshots["after_typing_text_x"] = state.text_x
                snapshots["after_typing_selected_kind"] = state.tree_entries[state.selected_idx].kind

                activate_tree_filter_selection()
                snapshots["after_enter_path"] = state.current_path.resolve()
                snapshots["after_enter_start"] = state.start
                snapshots["after_enter_text_x"] = state.text_x
                snapshots["after_enter_editing"] = state.tree_filter_editing

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.filter_panel.controller.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch("lazyviewer.runtime.app.os.isatty", return_value=True), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", current_file, "monokai", True, False)

            self.assertEqual(snapshots["after_typing_path"], snapshots["origin_path"])
            self.assertEqual(snapshots["after_typing_start"], snapshots["origin_start"])
            self.assertEqual(snapshots["after_typing_text_x"], snapshots["origin_text_x"])
            self.assertEqual(snapshots["after_typing_selected_kind"], "path")
            self.assertEqual(snapshots["after_enter_path"], snapshots["origin_path"])
            self.assertEqual(snapshots["after_enter_start"], snapshots["origin_start"])
            self.assertEqual(snapshots["after_enter_text_x"], snapshots["origin_text_x"])
            self.assertFalse(bool(snapshots["after_enter_editing"]))

    def test_content_search_escape_from_prompt_restores_original_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            current_file = root / "zzz.py"
            other_file = root / "aaa.py"
            current_file.write_text("line\n" * 120, encoding="utf-8")
            other_file.write_text("line\n" * 120, encoding="utf-8")
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, _query, _show_hidden, **_kwargs):
                return (
                    {
                        other_file.resolve(): [
                            ContentMatch(
                                path=other_file.resolve(),
                                line=25,
                                column=1,
                                preview="needle",
                            )
                        ],
                        current_file.resolve(): [
                            ContentMatch(
                                path=current_file.resolve(),
                                line=80,
                                column=1,
                                preview="needle",
                            )
                        ],
                    },
                    False,
                    None,
                )

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                open_tree_filter = _callback(kwargs, "open_tree_filter")
                apply_tree_filter_query = _callback(kwargs, "apply_tree_filter_query")
                activate_tree_filter_selection = _callback(kwargs, "activate_tree_filter_selection")
                close_tree_filter = _callback(kwargs, "close_tree_filter")
                state.start = 30
                state.text_x = 6
                snapshots["origin_path"] = state.current_path.resolve()
                snapshots["origin_start"] = state.start
                snapshots["origin_text_x"] = state.text_x

                open_tree_filter("content")
                apply_tree_filter_query("needle", preview_selection=False, select_first_file=False)
                state.selected_idx = next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.kind == "search_hit" and entry.path.resolve() == other_file.resolve()
                )
                activate_tree_filter_selection()
                snapshots["after_select_path"] = state.current_path.resolve()
                snapshots["after_select_start"] = state.start

                state.tree_filter_editing = True
                close_tree_filter(clear_query=True, restore_origin=True)
                snapshots["after_escape_path"] = state.current_path.resolve()
                snapshots["after_escape_start"] = state.start
                snapshots["after_escape_text_x"] = state.text_x
                snapshots["after_escape_active"] = state.tree_filter_active
                snapshots["after_escape_query"] = state.tree_filter_query

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.filter_panel.controller.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch("lazyviewer.runtime.app.os.isatty", return_value=True), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", current_file, "monokai", True, False)

            self.assertEqual(snapshots["after_select_path"], other_file.resolve())
            self.assertNotEqual(int(snapshots["after_select_start"]), int(snapshots["origin_start"]))
            self.assertEqual(snapshots["after_escape_path"], snapshots["origin_path"])
            self.assertEqual(snapshots["after_escape_start"], snapshots["origin_start"])
            self.assertEqual(snapshots["after_escape_text_x"], snapshots["origin_text_x"])
            self.assertFalse(bool(snapshots["after_escape_active"]))
            self.assertEqual(snapshots["after_escape_query"], "")

    def test_content_search_directory_arrow_click_collapses_and_reopens_subtree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            docs_dir = root / "docs"
            docs_dir.mkdir()
            docs_file = docs_dir / "readme.md"
            docs_file.write_text("needle in docs\n", encoding="utf-8")
            src_dir = root / "src"
            src_dir.mkdir()
            src_file = src_dir / "main.py"
            src_file.write_text("needle in src\n", encoding="utf-8")
            snapshots: dict[str, bool] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, _query, _show_hidden, **_kwargs):
                return (
                    {
                        docs_file.resolve(): [
                            ContentMatch(
                                path=docs_file.resolve(),
                                line=1,
                                column=1,
                                preview="    needle in docs",
                            )
                        ],
                        src_file.resolve(): [
                            ContentMatch(
                                path=src_file.resolve(),
                                line=1,
                                column=1,
                                preview="needle in src",
                            )
                        ],
                    },
                    False,
                    None,
                )

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                open_tree_filter = _callback(kwargs, "open_tree_filter")
                apply_tree_filter_query = _callback(kwargs, "apply_tree_filter_query")
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")

                open_tree_filter("content")
                apply_tree_filter_query("needle", preview_selection=False, select_first_file=True)

                docs_idx = next(
                    idx for idx, entry in enumerate(state.tree_entries) if entry.path.resolve() == docs_dir.resolve()
                )
                docs_entry = state.tree_entries[docs_idx]
                docs_row = (docs_idx - state.tree_start) + 2
                arrow_col = 1 + (docs_entry.depth * 2)
                snapshots["docs_child_visible_before"] = any(
                    entry.path.resolve() == docs_file.resolve() and entry.depth > docs_entry.depth
                    for entry in state.tree_entries
                )

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{arrow_col}:{docs_row}")
                snapshots["docs_collapsed"] = docs_dir.resolve() in state.tree_filter_collapsed_dirs
                snapshots["docs_child_hidden_after_close"] = not any(
                    entry.path.resolve() == docs_file.resolve() and entry.depth > docs_entry.depth
                    for entry in state.tree_entries
                )

                docs_idx = next(
                    idx for idx, entry in enumerate(state.tree_entries) if entry.path.resolve() == docs_dir.resolve()
                )
                docs_entry = state.tree_entries[docs_idx]
                docs_row = (docs_idx - state.tree_start) + 2
                arrow_col = 1 + (docs_entry.depth * 2)
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{arrow_col}:{docs_row}")

                snapshots["docs_reopened"] = docs_dir.resolve() not in state.tree_filter_collapsed_dirs
                snapshots["docs_child_visible_after_reopen"] = any(
                    entry.path.resolve() == docs_file.resolve() and entry.depth > docs_entry.depth
                    for entry in state.tree_entries
                )

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch(
                "lazyviewer.filter_panel.controller.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch(
                "lazyviewer.runtime.app.collect_project_file_labels", return_value=[]
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

            self.assertTrue(snapshots["docs_child_visible_before"])
            self.assertTrue(snapshots["docs_collapsed"])
            self.assertTrue(snapshots["docs_child_hidden_after_close"])
            self.assertTrue(snapshots["docs_reopened"])
            self.assertTrue(snapshots["docs_child_visible_after_reopen"])

    def test_content_search_selected_hit_stays_visible_when_help_toggles_on(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text("line\n" * 120, encoding="utf-8")
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, _query, _show_hidden, **_kwargs):
                return (
                    {
                        file_path.resolve(): [
                            ContentMatch(
                                path=file_path.resolve(),
                                line=120,
                                column=1,
                                preview="line",
                            )
                        ]
                    },
                    False,
                    None,
                )

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                open_tree_filter = _callback(kwargs, "open_tree_filter")
                apply_tree_filter_query = _callback(kwargs, "apply_tree_filter_query")
                handle_normal_key = _callback(kwargs, "handle_normal_key")

                open_tree_filter("content")
                apply_tree_filter_query("line", preview_selection=True, select_first_file=True)
                before_help_start = state.start

                handle_normal_key("?", 120)

                selected_entry = state.tree_entries[state.selected_idx]
                selected_line = (selected_entry.line or 1) - 1
                help_rows = help_panel_row_count(
                    state.usable,
                    state.show_help,
                    browser_visible=state.browser_visible,
                    tree_filter_active=state.tree_filter_active,
                    tree_filter_mode=state.tree_filter_mode,
                    tree_filter_editing=state.tree_filter_editing,
                )
                visible_rows = max(1, state.usable - help_rows)
                snapshots["before_help_start"] = before_help_start
                snapshots["after_help_start"] = state.start
                snapshots["selected_line"] = selected_line
                snapshots["visible_rows"] = visible_rows
                snapshots["show_help"] = state.show_help

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.filter_panel.controller.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch("lazyviewer.runtime.app.os.isatty", return_value=True), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.runtime.app.load_content_search_left_pane_percent",
                return_value=None,
                create=True,
            ), mock.patch(
                "lazyviewer.runtime.app.shutil.get_terminal_size", return_value=os.terminal_size((120, 24))
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertTrue(bool(snapshots["show_help"]))
            selected_line = int(snapshots["selected_line"])
            after_help_start = int(snapshots["after_help_start"])
            visible_rows = int(snapshots["visible_rows"])
            self.assertGreaterEqual(after_help_start, int(snapshots["before_help_start"]))
            self.assertGreaterEqual(selected_line, after_help_start)
            self.assertLessEqual(selected_line, after_help_start + visible_rows - 1)

    def test_content_search_uses_separate_left_pane_width_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "demo.py").write_text("print('x')\n", encoding="utf-8")
            snapshots: dict[str, int] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, _query, _show_hidden, **_kwargs):
                return {}, False, None

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                open_tree_filter = _callback(kwargs, "open_tree_filter")
                apply_tree_filter_query = _callback(kwargs, "apply_tree_filter_query")
                close_tree_filter = _callback(kwargs, "close_tree_filter")
                save_left_pane_width = _callback(kwargs, "save_left_pane_width")

                snapshots["initial_left"] = state.left_width
                save_left_pane_width(100, state.left_width)

                open_tree_filter("content")
                apply_tree_filter_query("needle", preview_selection=False, select_first_file=True)
                snapshots["content_left"] = state.left_width
                save_left_pane_width(100, state.left_width)

                close_tree_filter(clear_query=True)
                snapshots["restored_left"] = state.left_width
                save_left_pane_width(100, state.left_width)

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.filter_panel.controller.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch("lazyviewer.runtime.app.shutil.get_terminal_size", return_value=os.terminal_size((100, 24))), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=30.0
            ), mock.patch(
                "lazyviewer.runtime.app.load_content_search_left_pane_percent", return_value=65.0
            ), mock.patch(
                "lazyviewer.runtime.app.save_left_pane_percent"
            ) as save_normal, mock.patch(
                "lazyviewer.runtime.app.save_content_search_left_pane_percent"
            ) as save_content, mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertEqual(snapshots["initial_left"], 30)
            self.assertEqual(snapshots["content_left"], 65)
            self.assertEqual(snapshots["restored_left"], 30)
            save_content.assert_called_once_with(100, 65)
            self.assertEqual(save_normal.call_count, 2)

    def test_content_search_backspace_reuses_cached_rg_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "demo.py").write_text("alpha\nbeta\n", encoding="utf-8")
            snapshots: dict[str, int] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, _query, _show_hidden, **_kwargs):
                return {}, False, None

            def fake_run_main_loop(**kwargs) -> None:
                open_tree_filter = _callback(kwargs, "open_tree_filter")
                apply_tree_filter_query = _callback(kwargs, "apply_tree_filter_query")
                open_tree_filter("content")
                apply_tree_filter_query("a", preview_selection=False, select_first_file=True)
                apply_tree_filter_query("ab", preview_selection=False, select_first_file=True)
                apply_tree_filter_query("a", preview_selection=False, select_first_file=True)
                snapshots["done"] = 1

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.filter_panel.controller.search_project_content_rg", side_effect=fake_search_content
            ) as search_mock, mock.patch(
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

            self.assertEqual(snapshots.get("done"), 1)
            self.assertEqual(search_mock.call_count, 2)

