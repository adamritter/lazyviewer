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

class AppRuntimeSessionTestsPart1(unittest.TestCase):
    def test_named_marks_persist_between_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text("line 1\nline 2\n", encoding="utf-8")
            config_path = root / "lazyviewer.json"
            snapshots: dict[str, object] = {}
            run_count = 0

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                nonlocal run_count
                run_count += 1
                state = kwargs["state"]
                set_named_mark = _callback(kwargs, "set_named_mark")
                if run_count == 1:
                    state.start = 9
                    state.text_x = 4
                    self.assertTrue(set_named_mark("a"))
                    return
                snapshots["named_marks"] = dict(state.named_marks)

            with mock.patch("lazyviewer.runtime.config.CONFIG_PATH", config_path), mock.patch(
                "lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop
            ), mock.patch("lazyviewer.runtime.app.TerminalController", _FakeTerminalController), mock.patch(
                "lazyviewer.runtime.app.collect_project_file_labels", return_value=[]
            ), mock.patch("lazyviewer.runtime.app.os.isatty", return_value=True), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ):
                app_runtime.run_pager("", file_path, "monokai", True, False)
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertIn("named_marks", snapshots)
            loaded_mark = snapshots["named_marks"]["a"]
            self.assertEqual(loaded_mark, JumpLocation(path=file_path.resolve(), start=9, text_x=4))

    def test_runtime_render_shows_full_nested_sticky_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text(
                (
                    "class Outer:\n"
                    "    class Inner:\n"
                    "        def run(self):\n"
                    "            value = 2\n"
                    "            return value\n"
                ),
                encoding="utf-8",
            )
            writes: list[bytes] = []

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                state.browser_visible = False
                state.start = 4

                with mock.patch("lazyviewer.render.os.write", side_effect=lambda _fd, data: writes.append(data) or len(data)):
                    render_dual_page(
                        text_lines=state.lines,
                        text_start=state.start,
                        tree_entries=state.tree_entries,
                        tree_start=state.tree_start,
                        tree_selected=state.selected_idx,
                        max_lines=6,
                        current_path=state.current_path,
                        tree_root=state.tree_root,
                        expanded=state.tree_render_expanded,
                        width=120,
                        left_width=state.left_width,
                        text_x=state.text_x,
                        wrap_text=state.wrap_text,
                        browser_visible=state.browser_visible,
                        show_hidden=state.show_hidden,
                        show_help=state.show_help,
                        tree_filter_active=state.tree_filter_active,
                        tree_filter_mode=state.tree_filter_mode,
                        tree_filter_query=state.tree_filter_query,
                        tree_filter_editing=state.tree_filter_editing,
                        tree_filter_cursor_visible=False,
                        tree_filter_match_count=state.tree_filter_match_count,
                        tree_filter_truncated=state.tree_filter_truncated,
                        tree_filter_loading=state.tree_filter_loading,
                        tree_filter_spinner_frame=0,
                        tree_filter_prefix="p>",
                        tree_filter_placeholder="type to filter files",
                        picker_active=state.picker_active,
                        picker_mode=state.picker_mode,
                        picker_query=state.picker_query,
                        picker_items=state.picker_match_labels,
                        picker_selected=state.picker_selected,
                        picker_focus=state.picker_focus,
                        picker_list_start=state.picker_list_start,
                        picker_message=state.picker_message,
                        git_status_overlay=state.git_status_overlay,
                        tree_search_query="",
                        text_search_query="",
                        text_search_current_line=0,
                        text_search_current_column=0,
                        preview_is_git_diff=state.preview_is_git_diff,
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
                app_runtime.run_pager("", file_path, "monokai", True, False)

            rendered = b"".join(writes).decode("utf-8", errors="replace")
            outer_idx = rendered.find("class Outer:")
            inner_idx = rendered.find("class Inner:")
            run_idx = rendered.find("def run(self):")
            self.assertGreaterEqual(outer_idx, 0)
            self.assertGreater(inner_idx, outer_idx)
            self.assertGreater(run_idx, inner_idx)

    def test_runtime_down_scroll_advances_bottom_row_when_sticky_header_appears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text(
                (
                    "def run():\n"
                    "    first = 1\n"
                    "    second = 2\n"
                    "    third = 3\n"
                    "    fourth = 4\n"
                ),
                encoding="utf-8",
            )
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def render_content_rows(state) -> list[str]:
                writes: list[bytes] = []
                with mock.patch(
                    "lazyviewer.render.os.write",
                    side_effect=lambda _fd, data: writes.append(data) or len(data),
                ):
                    render_dual_page(
                        text_lines=state.lines,
                        text_start=state.start,
                        tree_entries=state.tree_entries,
                        tree_start=state.tree_start,
                        tree_selected=state.selected_idx,
                        max_lines=3,
                        current_path=state.current_path,
                        tree_root=state.tree_root,
                        expanded=state.tree_render_expanded,
                        width=120,
                        left_width=state.left_width,
                        text_x=state.text_x,
                        wrap_text=state.wrap_text,
                        browser_visible=state.browser_visible,
                        show_hidden=state.show_hidden,
                        show_help=state.show_help,
                        tree_filter_active=state.tree_filter_active,
                        tree_filter_mode=state.tree_filter_mode,
                        tree_filter_query=state.tree_filter_query,
                        tree_filter_editing=state.tree_filter_editing,
                        tree_filter_cursor_visible=False,
                        tree_filter_match_count=state.tree_filter_match_count,
                        tree_filter_truncated=state.tree_filter_truncated,
                        tree_filter_loading=state.tree_filter_loading,
                        tree_filter_spinner_frame=0,
                        tree_filter_prefix="p>",
                        tree_filter_placeholder="type to filter files",
                        picker_active=state.picker_active,
                        picker_mode=state.picker_mode,
                        picker_query=state.picker_query,
                        picker_items=state.picker_match_labels,
                        picker_selected=state.picker_selected,
                        picker_focus=state.picker_focus,
                        picker_list_start=state.picker_list_start,
                        picker_message=state.picker_message,
                        git_status_overlay=state.git_status_overlay,
                        tree_search_query="",
                        text_search_query="",
                        text_search_current_line=0,
                        text_search_current_column=0,
                        preview_is_git_diff=state.preview_is_git_diff,
                    )
                rendered = b"".join(writes).decode("utf-8", errors="replace")
                plain = ANSI_ESCAPE_RE.sub("", rendered)
                rows = [line for line in plain.split("\r\n") if line]
                return rows[:3]

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_normal_key = _callback(kwargs, "handle_normal_key")
                rebuild_screen_lines = _callback(kwargs, "rebuild_screen_lines")
                state.browser_visible = False
                state.usable = 3
                rebuild_screen_lines(columns=120, preserve_scroll=True)
                state.start = 0
                before_rows = render_content_rows(state)
                handle_normal_key("DOWN", 120)
                after_rows = render_content_rows(state)
                snapshots["before_rows"] = before_rows
                snapshots["after_rows"] = after_rows
                snapshots["start_after_down"] = state.start

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", file_path, "monokai", True, False)

            before_rows = snapshots["before_rows"]
            after_rows = snapshots["after_rows"]
            self.assertEqual(snapshots["start_after_down"], 1)
            self.assertIn("second = 2", before_rows[2])
            self.assertIn("def run():", after_rows[0])
            self.assertIn("third = 3", after_rows[2])
            self.assertNotIn("second = 2", after_rows[2])
