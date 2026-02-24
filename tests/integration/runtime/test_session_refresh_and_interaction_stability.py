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

class AppRuntimeSessionTestsPart2(unittest.TestCase):
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
                "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg", side_effect=fake_search_content
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
