"""Integration-heavy tests for ``lazyviewer.app_runtime`` wiring.

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

from lazyviewer import app_runtime
from lazyviewer.ansi import ANSI_ESCAPE_RE
from lazyviewer.app_runtime import (
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


class AppRuntimeGitTests(unittest.TestCase):
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.os.isatty", return_value=True
            ), mock.patch("lazyviewer.app_runtime.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.app_runtime.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.app_runtime.GIT_STATUS_REFRESH_SECONDS", 0.0
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.os.isatty", return_value=True
            ), mock.patch("lazyviewer.app_runtime.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.app_runtime.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.app_runtime.GIT_STATUS_REFRESH_SECONDS", 0.0
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.os.isatty", return_value=True
            ), mock.patch("lazyviewer.app_runtime.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.app_runtime.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.app_runtime.GIT_STATUS_REFRESH_SECONDS", 0.0
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertEqual(snapshots["after_n_path"], nested_file.resolve())
            self.assertEqual(snapshots["after_N_path"], root_file.resolve())
            self.assertEqual(snapshots["after_n2_path"], nested_file.resolve())
            self.assertEqual(snapshots["after_p_path"], root_file.resolve())
            self.assertEqual(snapshots["after_N2_path"], root_file.resolve())
            self.assertEqual(int(snapshots["after_p_start"]), int(snapshots["after_N_start"]))
            self.assertLess(int(snapshots["after_N2_start"]), int(snapshots["after_N_start"]))

    @unittest.skipIf(shutil.which("git") is None, "git is required for git watch integration test")
    def test_git_watch_refresh_rebuilds_preview_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

            file_path = root / "demo.py"
            file_path.write_text("a = 1\nb = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            file_path.write_text("a = 1\nb = 22\n", encoding="utf-8")
            snapshots: dict[str, str] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                maybe_refresh_git_watch = _callback(kwargs, "maybe_refresh_git_watch")
                snapshots["before_commit"] = state.rendered

                subprocess.run(["git", "add", "-A"], cwd=root, check=True)
                subprocess.run(["git", "commit", "-q", "-m", "after-edit"], cwd=root, check=True)
                maybe_refresh_git_watch()

                snapshots["after_commit"] = state.rendered

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.os.isatty", return_value=True
            ), mock.patch("lazyviewer.app_runtime.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.app_runtime.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.app_runtime.GIT_WATCH_POLL_SECONDS", 0.0
            ), mock.patch(
                "lazyviewer.app_runtime.GIT_STATUS_REFRESH_SECONDS", 0.0
            ):
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertIn("+ b = 22", snapshots["before_commit"])
            self.assertIn("- b = 2", snapshots["before_commit"])
            self.assertEqual(snapshots["after_commit"], "a = 1\nb = 22\n")

    @unittest.skipIf(shutil.which("git") is None, "git is required for hidden-toggle integration test")
    def test_hidden_toggle_turns_hidden_and_gitignored_on_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

            (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            pycache = root / "__pycache__"
            pycache.mkdir()
            (pycache / "demo.cpython-313.pyc").write_bytes(b"pyc")
            (root / "visible.txt").write_text("ok\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            snapshots: dict[str, set[str]] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_normal_key = _callback(kwargs, "handle_normal_key")

                def current_labels() -> set[str]:
                    labels: set[str] = set()
                    for entry in state.tree_entries:
                        resolved = entry.path.resolve()
                        if resolved == root:
                            labels.add(".")
                        else:
                            labels.add(resolved.relative_to(root).as_posix())
                    return labels

                snapshots["before"] = current_labels()
                handle_normal_key(".", 120)
                snapshots["after"] = current_labels()

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.os.isatty", return_value=True
            ), mock.patch("lazyviewer.app_runtime.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.app_runtime.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertNotIn(".git", snapshots["before"])
            self.assertNotIn(".gitignore", snapshots["before"])
            self.assertNotIn("__pycache__", snapshots["before"])
            self.assertIn(".git", snapshots["after"])
            self.assertIn(".gitignore", snapshots["after"])
            self.assertIn("__pycache__", snapshots["after"])

    def test_ctrl_g_launches_lazygit_and_ctrl_o_toggles_git_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "demo.py").write_text("x = 1\n", encoding="utf-8")
            snapshots: dict[str, bool] = {}

            class _FakeTerminalController:
                disable_calls = 0
                enable_calls = 0

                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

                def disable_tui_mode(self) -> None:
                    type(self).disable_calls += 1

                def enable_tui_mode(self) -> None:
                    type(self).enable_calls += 1

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_normal_key = _callback(kwargs, "handle_normal_key")
                snapshots["before_ctrl_o"] = state.git_features_enabled
                handle_normal_key("CTRL_O", 120)
                snapshots["after_ctrl_o"] = state.git_features_enabled
                handle_normal_key("CTRL_G", 120)

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.shutil.which", return_value="/usr/local/bin/lazygit"
            ), mock.patch("lazyviewer.app_runtime.subprocess.run") as lazygit_run, mock.patch(
                "lazyviewer.app_runtime.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.app_runtime.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.app_runtime.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            lazygit_calls = [
                call
                for call in lazygit_run.call_args_list
                if call.args and call.args[0] == ["lazygit"]
            ]
            self.assertEqual(len(lazygit_calls), 1)
            self.assertEqual(lazygit_calls[0].kwargs, {"cwd": root.resolve(), "check": False})
            self.assertTrue(snapshots["before_ctrl_o"])
            self.assertFalse(snapshots["after_ctrl_o"])
            self.assertEqual(_FakeTerminalController.disable_calls, 1)
            self.assertEqual(_FakeTerminalController.enable_calls, 1)

