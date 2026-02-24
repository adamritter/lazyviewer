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

class AppRuntimeGitTestsPart2(unittest.TestCase):
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

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.runtime.app.GIT_WATCH_POLL_SECONDS", 0.0
            ), mock.patch(
                "lazyviewer.runtime.app.GIT_STATUS_REFRESH_SECONDS", 0.0
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

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.shutil.which", return_value="/usr/local/bin/lazygit"
            ), mock.patch("lazyviewer.runtime.app.subprocess.run") as lazygit_run, mock.patch(
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
