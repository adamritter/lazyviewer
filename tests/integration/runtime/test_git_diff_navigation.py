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

class AppRuntimeGitTestsPart1(unittest.TestCase):
    @unittest.skipIf(shutil.which("git") is None, "git is required for git modified navigation tests")
    def test_n_shift_n_and_p_follow_tree_order_for_git_modified_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

            nested_dir = root / "zzz"
            nested_dir.mkdir()
            root_file = root / "aaa.py"
            nested_file = nested_dir / "inner.py"
            root_file.write_text("x = 1\n", encoding="utf-8")
            nested_file.write_text("y = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            root_file.write_text("x = 2\n", encoding="utf-8")
            nested_file.write_text("y = 2\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_normal_key = _callback(kwargs, "handle_normal_key")
                snapshots["initial"] = state.current_path.resolve()
                handle_normal_key("n", 120)
                snapshots["after_n_1"] = state.current_path.resolve()
                snapshots["after_n_1_status"] = state.status_message
                handle_normal_key("n", 120)
                snapshots["after_n_2"] = state.current_path.resolve()
                snapshots["after_n_2_status"] = state.status_message
                handle_normal_key("n", 120)
                snapshots["after_n_3"] = state.current_path.resolve()
                snapshots["after_n_3_status"] = state.status_message
                handle_normal_key("N", 120)
                snapshots["after_N"] = state.current_path.resolve()
                snapshots["after_N_status"] = state.status_message
                handle_normal_key("p", 120)
                snapshots["after_p"] = state.current_path.resolve()
                snapshots["after_p_status"] = state.status_message

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.runtime.app.GIT_STATUS_REFRESH_SECONDS", 0.0
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertEqual(snapshots["initial"], root)
            self.assertEqual(snapshots["after_n_1"], nested_file.resolve())
            self.assertEqual(snapshots["after_n_2"], root_file.resolve())
            self.assertEqual(snapshots["after_n_3"], nested_file.resolve())
            self.assertEqual(snapshots["after_N"], root_file.resolve())
            self.assertEqual(snapshots["after_p"], nested_file.resolve())
            self.assertEqual(snapshots["after_n_1_status"], "")
            self.assertEqual(snapshots["after_n_2_status"], "")
            self.assertEqual(snapshots["after_n_3_status"], "wrapped to first change")
            self.assertEqual(snapshots["after_N_status"], "wrapped to last change")
            self.assertEqual(snapshots["after_p_status"], "")

    @unittest.skipIf(shutil.which("git") is None, "git is required for same-file git hunk navigation tests")
    def test_n_shift_n_and_p_navigate_between_change_blocks_within_same_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

            file_path = root / "demo.py"
            original_lines = [f"line_{idx:03d} = {idx}\n" for idx in range(1, 161)]
            file_path.write_text("".join(original_lines), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            updated_lines = list(original_lines)
            updated_lines[9] = "line_010 = 'first-change'\n"
            updated_lines[119] = "line_120 = 'second-change'\n"
            file_path.write_text("".join(updated_lines), encoding="utf-8")
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_normal_key = _callback(kwargs, "handle_normal_key")
                snapshots["initial_path"] = state.current_path.resolve()
                snapshots["initial_start"] = state.start
                snapshots["initial_is_diff"] = state.preview_is_git_diff
                handle_normal_key("n", 120)
                snapshots["after_n_path"] = state.current_path.resolve()
                snapshots["after_n_start"] = state.start
                snapshots["after_n_status"] = state.status_message
                handle_normal_key("n", 120)
                snapshots["after_n_wrap_path"] = state.current_path.resolve()
                snapshots["after_n_wrap_start"] = state.start
                snapshots["after_n_wrap_status"] = state.status_message
                handle_normal_key("N", 120)
                snapshots["after_N_path"] = state.current_path.resolve()
                snapshots["after_N_start"] = state.start
                snapshots["after_N_status"] = state.status_message
                handle_normal_key("p", 120)
                snapshots["after_p_path"] = state.current_path.resolve()
                snapshots["after_p_start"] = state.start
                snapshots["after_p_status"] = state.status_message

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.runtime.app.GIT_STATUS_REFRESH_SECONDS", 0.0
            ):
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertTrue(bool(snapshots["initial_is_diff"]))
            self.assertEqual(snapshots["initial_path"], file_path.resolve())
            self.assertEqual(snapshots["after_n_path"], file_path.resolve())
            self.assertEqual(snapshots["after_n_wrap_path"], file_path.resolve())
            self.assertEqual(snapshots["after_N_path"], file_path.resolve())
            self.assertEqual(snapshots["after_p_path"], file_path.resolve())
            self.assertGreater(int(snapshots["after_n_start"]), int(snapshots["initial_start"]))
            self.assertEqual(snapshots["after_n_status"], "")
            self.assertLess(int(snapshots["after_n_wrap_start"]), int(snapshots["after_n_start"]))
            self.assertEqual(snapshots["after_n_wrap_status"], "wrapped to first change")
            self.assertGreater(int(snapshots["after_N_start"]), int(snapshots["after_n_wrap_start"]))
            self.assertEqual(snapshots["after_N_status"], "wrapped to last change")
            self.assertEqual(int(snapshots["after_p_start"]), int(snapshots["after_n_wrap_start"]))
            self.assertEqual(snapshots["after_p_status"], "")

    @unittest.skipIf(shutil.which("git") is None, "git is required for reverse cross-file hunk navigation tests")
    def test_shift_n_and_p_land_on_last_hunk_of_previous_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

            nested_dir = root / "zzz"
            nested_dir.mkdir()
            root_file = root / "aaa.py"
            nested_file = nested_dir / "inner.py"
            root_lines = [f"line_{idx:03d} = {idx}\n" for idx in range(1, 181)]
            root_file.write_text("".join(root_lines), encoding="utf-8")
            nested_file.write_text("nested = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            updated_root_lines = list(root_lines)
            updated_root_lines[9] = "line_010 = 'first-change'\n"
            updated_root_lines[139] = "line_140 = 'second-change'\n"
            root_file.write_text("".join(updated_root_lines), encoding="utf-8")
            nested_file.write_text("nested = 2\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_normal_key = _callback(kwargs, "handle_normal_key")
                handle_normal_key("n", 120)
                snapshots["after_n_path"] = state.current_path.resolve()
                snapshots["after_n_start"] = state.start
                handle_normal_key("N", 120)
                snapshots["after_N_path"] = state.current_path.resolve()
                snapshots["after_N_start"] = state.start
                handle_normal_key("n", 120)
                snapshots["after_n2_path"] = state.current_path.resolve()
                snapshots["after_n2_start"] = state.start
                handle_normal_key("p", 120)
                snapshots["after_p_path"] = state.current_path.resolve()
                snapshots["after_p_start"] = state.start
                handle_normal_key("N", 120)
                snapshots["after_N2_path"] = state.current_path.resolve()
                snapshots["after_N2_start"] = state.start

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.runtime.app.GIT_STATUS_REFRESH_SECONDS", 0.0
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertEqual(snapshots["after_n_path"], nested_file.resolve())
            self.assertEqual(snapshots["after_N_path"], root_file.resolve())
            self.assertEqual(snapshots["after_n2_path"], nested_file.resolve())
            self.assertEqual(snapshots["after_p_path"], root_file.resolve())
            self.assertEqual(snapshots["after_N2_path"], root_file.resolve())
            self.assertEqual(int(snapshots["after_p_start"]), int(snapshots["after_N_start"]))
            self.assertLess(int(snapshots["after_N2_start"]), int(snapshots["after_N_start"]))
