"""Integration-heavy tests for ``lazyviewer.runtime.app`` wiring.

Covers git/watch refresh behavior, key-driven state transitions, and search flows.
These tests ensure runtime callbacks and state orchestration stay coherent.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest import mock

from lazyviewer.runtime import app as app_runtime
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

class AppRuntimeContentSearchTestsPart1(unittest.TestCase):
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
                snapshots["current_path"] = state.workspace.current_path.resolve()
                snapshots["start"] = state.preview.scroll
                snapshots["selected_kind"] = state.workspace.entries[state.workspace.selected].kind if state.workspace.entries else ""

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.WorkspaceIndexWarmup.schedule", return_value=None), mock.patch(
                "lazyviewer.search.service.search_project_content_rg", side_effect=fake_search_content
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
                state.preview.scroll = 30
                state.preview.horizontal_scroll = 4
                snapshots["origin_path"] = state.workspace.current_path.resolve()
                snapshots["origin_start"] = state.preview.scroll
                snapshots["origin_text_x"] = state.preview.horizontal_scroll

                open_tree_filter("content")
                apply_tree_filter_query("needle", preview_selection=False, select_first_file=False)
                snapshots["after_typing_path"] = state.workspace.current_path.resolve()
                snapshots["after_typing_start"] = state.preview.scroll
                snapshots["after_typing_text_x"] = state.preview.horizontal_scroll
                snapshots["after_typing_selected_kind"] = state.workspace.entries[state.workspace.selected].kind

                activate_tree_filter_selection()
                snapshots["after_enter_path"] = state.workspace.current_path.resolve()
                snapshots["after_enter_start"] = state.preview.scroll
                snapshots["after_enter_text_x"] = state.preview.horizontal_scroll
                snapshots["after_enter_editing"] = state.filter.editing

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.WorkspaceIndexWarmup.schedule", return_value=None), mock.patch(
                "lazyviewer.search.service.search_project_content_rg", side_effect=fake_search_content
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
                state.preview.scroll = 30
                state.preview.horizontal_scroll = 6
                snapshots["origin_path"] = state.workspace.current_path.resolve()
                snapshots["origin_start"] = state.preview.scroll
                snapshots["origin_text_x"] = state.preview.horizontal_scroll

                open_tree_filter("content")
                apply_tree_filter_query("needle", preview_selection=False, select_first_file=False)
                state.workspace.selected = next(
                    idx
                    for idx, entry in enumerate(state.workspace.entries)
                    if entry.kind == "search_hit" and entry.path.resolve() == other_file.resolve()
                )
                activate_tree_filter_selection()
                snapshots["after_select_path"] = state.workspace.current_path.resolve()
                snapshots["after_select_start"] = state.preview.scroll

                state.filter.editing = True
                close_tree_filter(clear_query=True, restore_origin=True)
                snapshots["after_escape_path"] = state.workspace.current_path.resolve()
                snapshots["after_escape_start"] = state.preview.scroll
                snapshots["after_escape_text_x"] = state.preview.horizontal_scroll
                snapshots["after_escape_active"] = state.filter.active
                snapshots["after_escape_query"] = state.filter.query

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.WorkspaceIndexWarmup.schedule", return_value=None), mock.patch(
                "lazyviewer.search.service.search_project_content_rg", side_effect=fake_search_content
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
                    idx for idx, entry in enumerate(state.workspace.entries) if entry.path.resolve() == docs_dir.resolve()
                )
                docs_entry = state.workspace.entries[docs_idx]
                docs_row = (docs_idx - state.workspace.scroll) + 2
                arrow_col = 1 + (docs_entry.depth * 2)
                snapshots["docs_child_visible_before"] = any(
                    entry.path.resolve() == docs_file.resolve() and entry.depth > docs_entry.depth
                    for entry in state.workspace.entries
                )

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{arrow_col}:{docs_row}")
                snapshots["docs_collapsed"] = docs_dir.resolve() in state.filter.collapsed_dirs
                snapshots["docs_child_hidden_after_close"] = not any(
                    entry.path.resolve() == docs_file.resolve() and entry.depth > docs_entry.depth
                    for entry in state.workspace.entries
                )

                docs_idx = next(
                    idx for idx, entry in enumerate(state.workspace.entries) if entry.path.resolve() == docs_dir.resolve()
                )
                docs_entry = state.workspace.entries[docs_idx]
                docs_row = (docs_idx - state.workspace.scroll) + 2
                arrow_col = 1 + (docs_entry.depth * 2)
                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{arrow_col}:{docs_row}")

                snapshots["docs_reopened"] = docs_dir.resolve() not in state.filter.collapsed_dirs
                snapshots["docs_child_visible_after_reopen"] = any(
                    entry.path.resolve() == docs_file.resolve() and entry.depth > docs_entry.depth
                    for entry in state.workspace.entries
                )

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch(
                "lazyviewer.search.service.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch(
                "lazyviewer.runtime.app.WorkspaceIndexWarmup.schedule", return_value=None
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
