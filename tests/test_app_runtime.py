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


class AppRuntimeBehaviorTests(unittest.TestCase):
    def test_first_git_change_screen_line_handles_plain_and_ansi_markers(self) -> None:
        plain_lines = [
            "  unchanged",
            "- removed",
            "+ added",
        ]
        self.assertEqual(_first_git_change_screen_line(plain_lines), 1)

        ansi_lines = [
            "\033[2;38;5;245m  \033[0munchanged",
            "\033[38;5;42m+ \033[0madded",
        ]
        self.assertEqual(_first_git_change_screen_line(ansi_lines), 1)

        background_lines = [
            "\033[38;5;252munchanged\033[0m",
            "\033[38;5;252;48;5;22madded\033[0m",
        ]
        self.assertEqual(_first_git_change_screen_line(background_lines), 1)

        truecolor_background_lines = [
            "\033[38;5;252munchanged\033[0m",
            "\033[38;2;220;220;220;48;2;36;74;52madded\033[0m",
        ]
        self.assertEqual(_first_git_change_screen_line(truecolor_background_lines), 1)

    def test_first_git_change_screen_line_returns_none_without_markers(self) -> None:
        self.assertIsNone(_first_git_change_screen_line(["x = 1", "y = 2"]))

    def test_centered_scroll_start_clamps_and_interpolates(self) -> None:
        self.assertEqual(_centered_scroll_start(target_line=30, max_start=40, visible_rows=12), 26)
        self.assertEqual(_centered_scroll_start(target_line=1, max_start=40, visible_rows=12), 0)
        self.assertEqual(_centered_scroll_start(target_line=120, max_start=40, visible_rows=12), 36)

    def test_tree_order_key_matches_dirs_first_tree_sort(self) -> None:
        relative_paths = [
            Path("aaa.py"),
            Path("zzz/inner.py"),
            Path("bbb.py"),
            Path("bbb/aaa.py"),
            Path("zzz.py"),
        ]
        ordered = sorted(relative_paths, key=_tree_order_key_for_relative_path)
        self.assertEqual(
            ordered,
            [
                Path("bbb/aaa.py"),
                Path("zzz/inner.py"),
                Path("aaa.py"),
                Path("bbb.py"),
                Path("zzz.py"),
            ],
        )
        self.assertLess(
            _tree_order_key_for_relative_path(Path("zzz"), is_dir=True),
            _tree_order_key_for_relative_path(Path("zzz/inner.py")),
        )

    @unittest.skipIf(shutil.which("git") is None, "git is required for git modified navigation tests")
    def test_n_and_shift_n_follow_tree_order_for_git_modified_files(self) -> None:
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
            self.assertEqual(snapshots["after_n_1_status"], "")
            self.assertEqual(snapshots["after_n_2_status"], "")
            self.assertEqual(snapshots["after_n_3_status"], "wrapped to first change")
            self.assertEqual(snapshots["after_N_status"], "wrapped to last change")

    @unittest.skipIf(shutil.which("git") is None, "git is required for same-file git hunk navigation tests")
    def test_n_and_shift_n_navigate_between_change_blocks_within_same_file(self) -> None:
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
            self.assertGreater(int(snapshots["after_n_start"]), int(snapshots["initial_start"]))
            self.assertEqual(snapshots["after_n_status"], "")
            self.assertLess(int(snapshots["after_n_wrap_start"]), int(snapshots["after_n_start"]))
            self.assertEqual(snapshots["after_n_wrap_status"], "wrapped to first change")
            self.assertGreater(int(snapshots["after_N_start"]), int(snapshots["after_n_wrap_start"]))
            self.assertEqual(snapshots["after_N_status"], "wrapped to last change")

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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.app_runtime.subprocess.run") as subprocess_run, mock.patch(
                "lazyviewer.app_runtime.time.monotonic", return_value=100.0
            ), mock.patch("lazyviewer.app_runtime.os.isatty", return_value=True), mock.patch(
                "lazyviewer.app_runtime.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.app_runtime.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.app_runtime.subprocess.run") as subprocess_run, mock.patch(
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

    def test_single_click_identifier_in_preview_opens_content_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text("alpha_beta_name = other_value\n", encoding="utf-8")
            search_calls: list[str] = []
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, query, _show_hidden, **_kwargs):
                search_calls.append(query)
                if query == "alpha_beta_name":
                    return (
                        {
                            file_path.resolve(): [
                                ContentMatch(
                                    path=file_path.resolve(),
                                    line=1,
                                    column=1,
                                    preview="alpha_beta_name = other_value",
                                )
                            ]
                        },
                        False,
                        None,
                    )
                return {}, False, None

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                right_start_col = state.left_width + 2
                target_row: int | None = None
                target_col: int | None = None

                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line).rstrip("\r\n")
                    token_start = plain.find("alpha_beta_name")
                    if token_start >= 0:
                        target_row = idx + 1
                        target_col = right_start_col + token_start + len("alpha")
                        break

                self.assertIsNotNone(target_row)
                self.assertIsNotNone(target_col)
                assert target_row is not None
                assert target_col is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{target_col}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{target_col}:{target_row}")

                snapshots["tree_filter_active"] = state.tree_filter_active
                snapshots["tree_filter_mode"] = state.tree_filter_mode
                snapshots["tree_filter_query"] = state.tree_filter_query
                snapshots["tree_filter_editing"] = state.tree_filter_editing
                snapshots["source_selection_anchor"] = state.source_selection_anchor
                snapshots["source_selection_focus"] = state.source_selection_focus
                entry = state.tree_entries[state.selected_idx]
                snapshots["selected_kind"] = entry.kind
                snapshots["selected_path"] = entry.path.resolve()

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch(
                "lazyviewer.runtime_tree_filter.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch(
                "lazyviewer.app_runtime.collect_project_file_labels", return_value=[]
            ), mock.patch(
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
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertEqual(search_calls, ["alpha_beta_name"])
            self.assertTrue(bool(snapshots["tree_filter_active"]))
            self.assertEqual(snapshots["tree_filter_mode"], "content")
            self.assertEqual(snapshots["tree_filter_query"], "alpha_beta_name")
            self.assertFalse(bool(snapshots["tree_filter_editing"]))
            self.assertIsNone(snapshots["source_selection_anchor"])
            self.assertIsNone(snapshots["source_selection_focus"])
            self.assertEqual(snapshots["selected_kind"], "search_hit")
            self.assertEqual(snapshots["selected_path"], file_path.resolve())

    def test_clicking_directory_name_in_preview_selects_it_in_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            docs_dir = root / "docs"
            docs_dir.mkdir()
            (docs_dir / "guide.md").write_text("# guide\n", encoding="utf-8")
            (root / "main.py").write_text("print('x')\n", encoding="utf-8")
            snapshots: dict[str, Path] = {}

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

                target_row = None
                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line)
                    if "docs/" in plain:
                        target_row = idx + 1
                        break
                self.assertIsNotNone(target_row)
                assert target_row is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 3}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 3}:{target_row}")

                snapshots["current_path"] = state.current_path.resolve()
                snapshots["selected_path"] = state.tree_entries[state.selected_idx].path.resolve()

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
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

            self.assertEqual(snapshots["current_path"], docs_dir.resolve())
            self.assertEqual(snapshots["selected_path"], docs_dir.resolve())

    def test_clicking_file_name_in_preview_selects_it_in_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target_file = root / "README.md"
            target_file.write_text("hello\n", encoding="utf-8")
            (root / "other.txt").write_text("x\n", encoding="utf-8")
            snapshots: dict[str, Path] = {}

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

                target_row = None
                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line)
                    if "README.md" in plain:
                        target_row = idx + 1
                        break
                self.assertIsNotNone(target_row)
                assert target_row is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 3}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 3}:{target_row}")

                snapshots["current_path"] = state.current_path.resolve()
                snapshots["selected_path"] = state.tree_entries[state.selected_idx].path.resolve()

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
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

            self.assertEqual(snapshots["current_path"], target_file.resolve())
            self.assertEqual(snapshots["selected_path"], target_file.resolve())

    def test_clicking_nested_file_name_in_preview_keeps_full_nested_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp) / "lazyviewer").resolve()
            root.mkdir()
            nested_dir = root / "lazyviewer"
            nested_dir.mkdir()
            target_file = nested_dir / "app_runtime.py"
            target_file.write_text("print('nested')\n", encoding="utf-8")
            # Also create same leaf name at root to catch parent-level mis-resolution.
            (root / "app_runtime.py").write_text("print('root')\n", encoding="utf-8")
            snapshots: dict[str, Path] = {}

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

                target_row = None
                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line)
                    if "└─ app_runtime.py" in plain or "├─ app_runtime.py" in plain:
                        if "lazyviewer" in plain:
                            # This line is likely the nested one in narrow panes.
                            pass
                    if "app_runtime.py" in plain and ("│  " in plain or "   " in plain):
                        target_row = idx + 1
                        break
                self.assertIsNotNone(target_row)
                assert target_row is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 3}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 3}:{target_row}")

                snapshots["current_path"] = state.current_path.resolve()
                snapshots["selected_path"] = state.tree_entries[state.selected_idx].path.resolve()

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
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

            self.assertEqual(snapshots["current_path"], target_file.resolve())
            self.assertEqual(snapshots["selected_path"], target_file.resolve())

    def test_clicking_nested_file_name_in_preview_with_git_badge_keeps_full_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = (Path(tmp) / "lazyviewer").resolve()
            root.mkdir()
            nested_dir = root / "lazyviewer"
            nested_dir.mkdir()
            target_file = nested_dir / "app_runtime.py"
            target_file.write_text("print('nested')\n", encoding="utf-8")
            snapshots: dict[str, Path] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                refresh_git_status_overlay = _callback(kwargs, "refresh_git_status_overlay")
                right_start_col = state.left_width + 2

                refresh_git_status_overlay(force=True)

                target_row = None
                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line)
                    if "app_runtime.py" in plain and "[M]" in plain and ("│  " in plain or "   " in plain):
                        target_row = idx + 1
                        break
                self.assertIsNotNone(target_row)
                assert target_row is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 3}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 3}:{target_row}")

                snapshots["current_path"] = state.current_path.resolve()
                snapshots["selected_path"] = state.tree_entries[state.selected_idx].path.resolve()

            overlay = {target_file.resolve(): GIT_STATUS_CHANGED, nested_dir.resolve(): GIT_STATUS_CHANGED}
            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.collect_git_status_overlay", return_value=overlay
            ), mock.patch(
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

            self.assertEqual(snapshots["current_path"], target_file.resolve())
            self.assertEqual(snapshots["selected_path"], target_file.resolve())

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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.app_runtime.subprocess.run"), mock.patch(
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.app_runtime.subprocess.run"), mock.patch(
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.shutil.which", side_effect=fake_which
            ), mock.patch("lazyviewer.app_runtime.subprocess.run"), mock.patch(
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
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
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertTrue(bool(snapshots["handled"]))
            self.assertEqual(snapshots["before"], 0)
            self.assertEqual(snapshots["after"], 0)

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
                "lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop
            ), mock.patch("lazyviewer.app_runtime.TerminalController", _FakeTerminalController), mock.patch(
                "lazyviewer.app_runtime.collect_project_file_labels", return_value=[]
            ), mock.patch("lazyviewer.app_runtime.os.isatty", return_value=True), mock.patch(
                "lazyviewer.app_runtime.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ):
                app_runtime.run_pager("", file_path, "monokai", True, False)
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertIn("named_marks", snapshots)
            loaded_mark = snapshots["named_marks"]["a"]
            self.assertEqual(loaded_mark, JumpLocation(path=file_path.resolve(), start=9, text_x=4))

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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime_tree_filter.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch("lazyviewer.app_runtime.os.isatty", return_value=True), mock.patch(
                "lazyviewer.app_runtime.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.app_runtime.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertEqual(snapshots["current_path"], file_path.resolve())
            self.assertEqual(snapshots["selected_kind"], "search_hit")
            self.assertGreater(int(snapshots["start"]), 0)

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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch(
                "lazyviewer.runtime_tree_filter.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch(
                "lazyviewer.app_runtime.collect_project_file_labels", return_value=[]
            ), mock.patch(
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

            self.assertTrue(snapshots["docs_child_visible_before"])
            self.assertTrue(snapshots["docs_collapsed"])
            self.assertTrue(snapshots["docs_child_hidden_after_close"])
            self.assertTrue(snapshots["docs_reopened"])
            self.assertTrue(snapshots["docs_child_visible_after_reopen"])

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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.os.isatty", return_value=True
            ), mock.patch("lazyviewer.app_runtime.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.app_runtime.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.os.isatty", return_value=True
            ), mock.patch("lazyviewer.app_runtime.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.app_runtime.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", file_path, "monokai", True, False)

            before_rows = snapshots["before_rows"]
            after_rows = snapshots["after_rows"]
            self.assertEqual(snapshots["start_after_down"], 1)
            self.assertIn("second = 2", before_rows[2])
            self.assertIn("def run():", after_rows[0])
            self.assertIn("third = 3", after_rows[2])
            self.assertNotIn("second = 2", after_rows[2])

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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime_tree_filter.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch("lazyviewer.app_runtime.os.isatty", return_value=True), mock.patch(
                "lazyviewer.app_runtime.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.app_runtime.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.app_runtime.load_content_search_left_pane_percent",
                return_value=None,
                create=True,
            ), mock.patch(
                "lazyviewer.app_runtime.shutil.get_terminal_size", return_value=os.terminal_size((120, 24))
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertTrue(bool(snapshots["show_help"]))
            selected_line = int(snapshots["selected_line"])
            after_help_start = int(snapshots["after_help_start"])
            visible_rows = int(snapshots["visible_rows"])
            self.assertGreaterEqual(after_help_start, int(snapshots["before_help_start"]))
            self.assertGreaterEqual(selected_line, after_help_start)
            self.assertLessEqual(selected_line, after_help_start + visible_rows - 1)

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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.app_runtime.launch_editor", side_effect=fake_launch_editor
            ), mock.patch("lazyviewer.app_runtime.os.isatty", return_value=True), mock.patch(
                "lazyviewer.app_runtime.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.app_runtime.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertFalse(bool(snapshots["before_has_created"]))
            self.assertTrue(bool(snapshots["after_has_created"]))
            self.assertEqual(snapshots["current_path"], root)

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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime_tree_filter.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch("lazyviewer.app_runtime.shutil.get_terminal_size", return_value=os.terminal_size((100, 24))), mock.patch(
                "lazyviewer.app_runtime.load_left_pane_percent", return_value=30.0
            ), mock.patch(
                "lazyviewer.app_runtime.load_content_search_left_pane_percent", return_value=65.0
            ), mock.patch(
                "lazyviewer.app_runtime.save_left_pane_percent"
            ) as save_normal, mock.patch(
                "lazyviewer.app_runtime.save_content_search_left_pane_percent"
            ) as save_content, mock.patch(
                "lazyviewer.app_runtime.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.app_runtime.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.app_runtime.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.app_runtime.load_show_hidden", return_value=False
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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime_tree_filter.search_project_content_rg", side_effect=fake_search_content
            ) as search_mock, mock.patch(
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

            self.assertEqual(snapshots.get("done"), 1)
            self.assertEqual(search_mock.call_count, 2)

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

            with mock.patch("lazyviewer.app_runtime.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.app_runtime.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.app_runtime.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime_tree_filter.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch(
                "lazyviewer.app_runtime._copy_text_to_clipboard", return_value=True
            ), mock.patch(
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

            self.assertGreater(int(snapshots["transitions"]), 500)
            self.assertGreaterEqual(int(snapshots["final_start"]), 0)
            self.assertGreaterEqual(int(snapshots["final_text_x"]), 0)
            self.assertTrue(Path(snapshots["final_path"]).exists())


if __name__ == "__main__":
    unittest.main()
