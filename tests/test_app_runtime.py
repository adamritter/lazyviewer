from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from lazyviewer import app_runtime
from lazyviewer.app_runtime import (
    _centered_scroll_start,
    _first_git_change_screen_line,
    _tree_order_key_for_relative_path,
)
from lazyviewer.render import help_panel_row_count
from lazyviewer.search import ContentMatch


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
            snapshots: dict[str, Path] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_normal_key = kwargs["handle_normal_key"]
                snapshots["initial"] = state.current_path.resolve()
                handle_normal_key("n", 120)
                snapshots["after_n_1"] = state.current_path.resolve()
                handle_normal_key("n", 120)
                snapshots["after_n_2"] = state.current_path.resolve()
                handle_normal_key("N", 120)
                snapshots["after_N"] = state.current_path.resolve()

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
            self.assertEqual(snapshots["after_N"], nested_file.resolve())

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
                maybe_refresh_git_watch = kwargs["maybe_refresh_git_watch"]
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
                handle_normal_key = kwargs["handle_normal_key"]

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
                open_tree_filter = kwargs["open_tree_filter"]
                apply_tree_filter_query = kwargs["apply_tree_filter_query"]
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
                open_tree_filter = kwargs["open_tree_filter"]
                apply_tree_filter_query = kwargs["apply_tree_filter_query"]
                handle_normal_key = kwargs["handle_normal_key"]

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
                handle_normal_key = kwargs["handle_normal_key"]
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
                open_tree_filter = kwargs["open_tree_filter"]
                apply_tree_filter_query = kwargs["apply_tree_filter_query"]
                close_tree_filter = kwargs["close_tree_filter"]
                save_left_pane_width = kwargs["save_left_pane_width"]

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
                open_tree_filter = kwargs["open_tree_filter"]
                apply_tree_filter_query = kwargs["apply_tree_filter_query"]
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


if __name__ == "__main__":
    unittest.main()
