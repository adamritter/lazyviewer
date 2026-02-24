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
from lazyviewer.navigation import JumpLocation
from lazyviewer.render import help_panel_row_count, render_dual_page
from lazyviewer.search.content import ContentMatch


def _callback(kwargs: dict[str, object], name: str):
    callbacks = kwargs["callbacks"]
    return getattr(callbacks, name)


class AppRuntimeSessionTests(unittest.TestCase):
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

            with mock.patch("lazyviewer.config.CONFIG_PATH", config_path), mock.patch(
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

    def test_editing_directory_rebuilds_tree_and_shows_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "existing.txt").write_text("existing\n", encoding="utf-8")
            created = root / "created-from-editor.txt"
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

                def disable_tui_mode(self) -> None:
                    return

                def enable_tui_mode(self) -> None:
                    return

            def fake_launch_editor(target: Path, _disable, _enable) -> str | None:
                if target.resolve() == root:
                    created.write_text("new\n", encoding="utf-8")
                return None

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_normal_key = _callback(kwargs, "handle_normal_key")
                before = {entry.path.resolve() for entry in state.tree_entries}
                snapshots["before_has_created"] = created.resolve() in before
                handle_normal_key("e", 120)
                after = {entry.path.resolve() for entry in state.tree_entries}
                snapshots["after_has_created"] = created.resolve() in after
                snapshots["current_path"] = state.current_path.resolve()

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.launch_editor", side_effect=fake_launch_editor
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

            self.assertFalse(bool(snapshots["before_has_created"]))
            self.assertTrue(bool(snapshots["after_has_created"]))
            self.assertEqual(snapshots["current_path"], root)

    @unittest.skipIf(shutil.which("git") is None, "git is required for long-session stability integration test")
    def test_long_session_mixed_interactions_remain_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

            src_dir = root / "src"
            pkg_dir = src_dir / "pkg"
            pkg_dir.mkdir(parents=True)
            file_main = src_dir / "main.py"
            file_main.write_text(
                "".join(f"def fn_{idx}():\n    return {'x' * 180}\n\n" for idx in range(1, 120)),
                encoding="utf-8",
            )
            file_pkg = pkg_dir / "module.py"
            file_pkg.write_text("value = 1\n", encoding="utf-8")
            notes = root / "notes.txt"
            notes.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            file_main.write_text(
                "".join(f"def fn_{idx}():\n    return {'y' * 180}\n\n" for idx in range(1, 120)),
                encoding="utf-8",
            )
            file_pkg.write_text("value = 2\n", encoding="utf-8")
            (root / "scratch.py").write_text("needle = 1\n", encoding="utf-8")

            snapshots: dict[str, object] = {}

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
                handle_normal_key = _callback(kwargs, "handle_normal_key")
                handle_tree_mouse_wheel = _callback(kwargs, "handle_tree_mouse_wheel")
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                open_tree_filter = _callback(kwargs, "open_tree_filter")
                apply_tree_filter_query = _callback(kwargs, "apply_tree_filter_query")
                close_tree_filter = _callback(kwargs, "close_tree_filter")
                maybe_refresh_git_watch = _callback(kwargs, "maybe_refresh_git_watch")
                refresh_git_status_overlay = _callback(kwargs, "refresh_git_status_overlay")
                tick_source_selection_drag = _callback(kwargs, "tick_source_selection_drag")

                def assert_state_coherent() -> None:
                    self.assertTrue(state.tree_entries)
                    self.assertGreaterEqual(state.selected_idx, 0)
                    self.assertLess(state.selected_idx, len(state.tree_entries))
                    self.assertGreaterEqual(state.tree_start, 0)
                    self.assertLessEqual(state.tree_start, max(0, len(state.tree_entries) - 1))
                    self.assertGreaterEqual(state.start, 0)
                    self.assertGreaterEqual(state.max_start, 0)
                    self.assertLessEqual(state.start, state.max_start)
                    self.assertGreaterEqual(state.text_x, 0)
                    self.assertTrue(state.current_path.resolve().exists())

                refresh_git_status_overlay(force=True)
                assert_state_coherent()
                transitions = 0

                for idx in range(90):
                    handle_tree_mouse_wheel(f"MOUSE_WHEEL_DOWN:{state.left_width + 2}:1")
                    handle_tree_mouse_wheel(f"MOUSE_WHEEL_UP:{state.left_width + 2}:1")
                    handle_tree_mouse_wheel(f"MOUSE_WHEEL_RIGHT:{state.left_width + 2}:1")
                    handle_tree_mouse_wheel(f"MOUSE_WHEEL_LEFT:{state.left_width + 2}:1")
                    handle_normal_key("DOWN", 120)
                    handle_normal_key("UP", 120)
                    transitions += 6

                    if idx % 9 == 0:
                        handle_normal_key("?", 120)
                        transitions += 1
                    if idx % 10 == 0:
                        handle_normal_key("w", 120)
                        transitions += 1
                    if idx % 11 == 0:
                        handle_normal_key("t", 120)
                        transitions += 1
                    if idx % 13 == 0:
                        handle_normal_key("CTRL_O", 120)
                        handle_normal_key("CTRL_O", 120)
                        transitions += 2
                    if idx % 14 == 0:
                        maybe_refresh_git_watch()
                        refresh_git_status_overlay(force=True)
                        transitions += 2
                    if idx % 8 == 0:
                        handle_normal_key("n", 120)
                        handle_normal_key("N", 120)
                        transitions += 2
                    if idx % 15 == 0:
                        open_tree_filter("files")
                        apply_tree_filter_query("py", preview_selection=True, select_first_file=True)
                        close_tree_filter(clear_query=True)
                        open_tree_filter("content")
                        apply_tree_filter_query("needle", preview_selection=True, select_first_file=True)
                        close_tree_filter(clear_query=True)
                        transitions += 6

                    if idx == 30:
                        if not state.browser_visible:
                            handle_normal_key("t", 120)
                            transitions += 1
                        right_start_col = state.left_width + 2
                        right_edge_col = right_start_col + state.right_width - 1
                        handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 3}:2")
                        handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_edge_col}:2")
                        if tick_source_selection_drag is not None:
                            for _ in range(5):
                                tick_source_selection_drag()
                                transitions += 1
                        handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_edge_col}:2")
                        transitions += 3

                    assert_state_coherent()

                snapshots["transitions"] = transitions
                snapshots["final_start"] = state.start
                snapshots["final_text_x"] = state.text_x
                snapshots["final_path"] = state.current_path.resolve()

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.filter_panel.controller.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch(
                "lazyviewer.runtime.app._copy_text_to_clipboard", return_value=True
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

            self.assertGreater(int(snapshots["transitions"]), 500)
            self.assertGreaterEqual(int(snapshots["final_start"]), 0)
            self.assertGreaterEqual(int(snapshots["final_text_x"]), 0)
            self.assertTrue(Path(snapshots["final_path"]).exists())

