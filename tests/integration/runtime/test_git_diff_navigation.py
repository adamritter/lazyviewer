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

class AppRuntimeGitTestsPart1(unittest.TestCase):
    @unittest.skipIf(shutil.which("git") is None, "git is required for git diff color integration tests")
    def test_git_diff_preview_does_not_emit_default_foreground_on_diff_background(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

            file_path = root / "demo.py"
            file_path.write_text("def x():\n    pass\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            file_path.write_text(
                (
                    "def test_apply_line_background_keeps_last_character_readable_after_default_reset(self) -> None:\n"
                    '    line = "\\033[90mhead\\033[39;49;00mZ"\n'
                    "    rendered = _apply_line_background(line, _ADDED_BG_SGR)\n"
                ),
                encoding="utf-8",
            )
            snapshots: dict[str, str] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def _row_has_default_fg_on_diff_bg(row: str) -> bool:
                fg = "default"
                bg = "default"
                idx = 0
                while idx < len(row):
                    if row[idx] == "\x1b":
                        match = ANSI_ESCAPE_RE.match(row, idx)
                        if match is not None:
                            seq = match.group(0)
                            if seq.endswith("m"):
                                params = seq[2:-1]
                                parts = [part for part in params.split(";") if part]
                                if not parts:
                                    parts = ["0"]
                                part_idx = 0
                                while part_idx < len(parts):
                                    part = parts[part_idx]
                                    try:
                                        token = int(part)
                                    except ValueError:
                                        part_idx += 1
                                        continue
                                    if token == 0:
                                        fg = "default"
                                        bg = "default"
                                    elif token == 39:
                                        fg = "default"
                                    elif token == 49:
                                        bg = "default"
                                    elif 30 <= token <= 37 or 90 <= token <= 97:
                                        fg = str(token)
                                    elif 40 <= token <= 47 or 100 <= token <= 107:
                                        bg = str(token)
                                    elif token in {38, 48} and part_idx + 1 < len(parts):
                                        mode = parts[part_idx + 1]
                                        if mode == "5" and part_idx + 2 < len(parts):
                                            if token == 38:
                                                fg = f"38;5;{parts[part_idx + 2]}"
                                            else:
                                                bg = f"48;5;{parts[part_idx + 2]}"
                                            part_idx += 2
                                        elif mode == "2" and part_idx + 4 < len(parts):
                                            if token == 38:
                                                fg = ";".join(["38", "2", *parts[part_idx + 2 : part_idx + 5]])
                                            else:
                                                bg = ";".join(["48", "2", *parts[part_idx + 2 : part_idx + 5]])
                                            part_idx += 4
                                    part_idx += 1
                            idx = match.end()
                            continue

                    char = row[idx]
                    if (
                        char not in "\r\n"
                        and not char.isspace()
                        and fg == "default"
                        and bg in {"48;2;36;74;52", "48;2;92;43;49"}
                    ):
                        return True
                    idx += 1
                return False

            def _row_has_low_contrast_trailing_fg_on_diff_bg(row: str) -> bool:
                xterm_16 = (
                    (0, 0, 0),
                    (128, 0, 0),
                    (0, 128, 0),
                    (128, 128, 0),
                    (0, 0, 128),
                    (128, 0, 128),
                    (0, 128, 128),
                    (192, 192, 192),
                    (128, 128, 128),
                    (255, 0, 0),
                    (0, 255, 0),
                    (255, 255, 0),
                    (0, 0, 255),
                    (255, 0, 255),
                    (0, 255, 255),
                    (255, 255, 255),
                )

                def _xterm_256_rgb(color_index: int) -> tuple[int, int, int] | None:
                    if color_index < 0 or color_index > 255:
                        return None
                    if color_index <= 15:
                        return xterm_16[color_index]
                    if 16 <= color_index <= 231:
                        cube = color_index - 16
                        red_idx = cube // 36
                        green_idx = (cube % 36) // 6
                        blue_idx = cube % 6
                        steps = (0, 95, 135, 175, 215, 255)
                        return (steps[red_idx], steps[green_idx], steps[blue_idx])
                    gray = 8 + (color_index - 232) * 10
                    return (gray, gray, gray)

                def _foreground_rgb(token: str) -> tuple[int, int, int] | None:
                    if token == "default":
                        return (0, 0, 0)
                    if token.isdigit():
                        token_num = int(token)
                        if 30 <= token_num <= 37:
                            return xterm_16[token_num - 30]
                        if 90 <= token_num <= 97:
                            return xterm_16[token_num - 90 + 8]
                    if token.startswith("38;5;"):
                        try:
                            color_index = int(token.split(";")[2])
                        except (IndexError, ValueError):
                            return None
                        return _xterm_256_rgb(color_index)
                    if token.startswith("38;2;"):
                        parts = token.split(";")
                        if len(parts) != 5:
                            return None
                        try:
                            return (int(parts[2]), int(parts[3]), int(parts[4]))
                        except ValueError:
                            return None
                    return None

                def _relative_luminance(rgb: tuple[int, int, int]) -> float:
                    def _channel_to_linear(channel: int) -> float:
                        value = max(0, min(channel, 255)) / 255.0
                        if value <= 0.04045:
                            return value / 12.92
                        return ((value + 0.055) / 1.055) ** 2.4

                    red, green, blue = rgb
                    return (
                        0.2126 * _channel_to_linear(red)
                        + 0.7152 * _channel_to_linear(green)
                        + 0.0722 * _channel_to_linear(blue)
                    )

                def _contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
                    a_lum = _relative_luminance(a)
                    b_lum = _relative_luminance(b)
                    light = max(a_lum, b_lum)
                    dark = min(a_lum, b_lum)
                    return (light + 0.05) / (dark + 0.05)

                fg = "default"
                bg = "default"
                idx = 0
                trailing_char_fg: str | None = None
                trailing_char_bg: str | None = None
                while idx < len(row):
                    if row[idx] == "\x1b":
                        match = ANSI_ESCAPE_RE.match(row, idx)
                        if match is not None:
                            seq = match.group(0)
                            if seq.endswith("m"):
                                params = seq[2:-1]
                                parts = [part for part in params.split(";") if part]
                                if not parts:
                                    parts = ["0"]
                                part_idx = 0
                                while part_idx < len(parts):
                                    part = parts[part_idx]
                                    try:
                                        token = int(part)
                                    except ValueError:
                                        part_idx += 1
                                        continue
                                    if token == 0:
                                        fg = "default"
                                        bg = "default"
                                    elif token == 39:
                                        fg = "default"
                                    elif token == 49:
                                        bg = "default"
                                    elif 30 <= token <= 37 or 90 <= token <= 97:
                                        fg = str(token)
                                    elif 40 <= token <= 47 or 100 <= token <= 107:
                                        bg = str(token)
                                    elif token in {38, 48} and part_idx + 1 < len(parts):
                                        mode = parts[part_idx + 1]
                                        if mode == "5" and part_idx + 2 < len(parts):
                                            if token == 38:
                                                fg = f"38;5;{parts[part_idx + 2]}"
                                            else:
                                                bg = f"48;5;{parts[part_idx + 2]}"
                                            part_idx += 2
                                        elif mode == "2" and part_idx + 4 < len(parts):
                                            if token == 38:
                                                fg = ";".join(["38", "2", *parts[part_idx + 2 : part_idx + 5]])
                                            else:
                                                bg = ";".join(["48", "2", *parts[part_idx + 2 : part_idx + 5]])
                                            part_idx += 4
                                    part_idx += 1
                            idx = match.end()
                            continue
                    char = row[idx]
                    if char not in "\r\n" and not char.isspace():
                        trailing_char_fg = fg
                        trailing_char_bg = bg
                    idx += 1

                if trailing_char_fg is None or trailing_char_bg is None:
                    return False
                background_rgb = None
                if trailing_char_bg == "48;2;36;74;52":
                    background_rgb = (36, 74, 52)
                elif trailing_char_bg == "48;2;92;43;49":
                    background_rgb = (92, 43, 49)
                if background_rgb is None:
                    return False
                foreground_rgb = _foreground_rgb(trailing_char_fg)
                if foreground_rgb is None:
                    return False
                return _contrast_ratio(foreground_rgb, background_rgb) < 3.0

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                self.assertTrue(state.preview_is_git_diff)
                state.browser_visible = False
                state.text_x = 0

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
                        max_lines=8,
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
                snapshots["rendered"] = b"".join(writes).decode("utf-8", errors="replace")

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.source_pane.path.os.isatty", return_value=True
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.source_pane.path.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.runtime.app.GIT_STATUS_REFRESH_SECONDS", 0.0
            ):
                app_runtime.run_pager("", file_path, "monokai", False, False)

            rendered = snapshots["rendered"]
            rows = rendered.split("\r\n")
            diff_rows = [
                row
                for row in rows
                if "48;2;36;74;52m" in row or "48;2;92;43;49m" in row
            ]
            self.assertTrue(diff_rows)
            self.assertFalse(any(_row_has_default_fg_on_diff_bg(row) for row in diff_rows))
            # Guard against unreadable default-foreground resets on diff rows.
            # Normal syntax colors are allowed to remain unchanged at row edges.

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

    @unittest.skipIf(shutil.which("git") is None, "git is required for git modified navigation tests")
    def test_n_jump_keeps_changed_hunk_visible_when_syntax_uses_background(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

            file_path = root / "demo.py"
            original_lines = [f"line_{idx:03d} = {idx}\n" for idx in range(1, 241)]
            file_path.write_text("".join(original_lines), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            updated_lines = list(original_lines)
            updated_lines[199] = "line_200 = 'hunk-target-visible'\n"
            file_path.write_text("".join(updated_lines), encoding="utf-8")
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def _background_colorize(source: str, _target: Path, _style: str) -> str:
                return "\n".join(f"\033[48;5;17m{line}\033[0m" for line in source.splitlines())

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_normal_key = _callback(kwargs, "handle_normal_key")
                handle_normal_key("n", 120)
                snapshots["path"] = state.current_path.resolve()
                snapshots["is_diff"] = state.preview_is_git_diff
                visible_rows = max(1, len(state.lines) - state.max_start)
                window_start = max(0, min(state.start, state.max_start))
                window_end = min(len(state.lines), window_start + visible_rows)
                visible_text = "\n".join(
                    ANSI_ESCAPE_RE.sub("", row)
                    for row in state.lines[window_start:window_end]
                )
                snapshots["visible_text"] = visible_text

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.source_pane.path.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.source_pane.diff.colorize_source", side_effect=_background_colorize
            ), mock.patch(
                "lazyviewer.source_pane.path.colorize_source", side_effect=_background_colorize
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.source_pane.path.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.runtime.app.GIT_STATUS_REFRESH_SECONDS", 0.0
            ):
                app_runtime.run_pager("", root, "monokai", False, False)

            self.assertEqual(snapshots["path"], file_path.resolve())
            self.assertTrue(bool(snapshots["is_diff"]))
            self.assertIn("hunk-target-visible", str(snapshots["visible_text"]))

    @unittest.skipIf(shutil.which("git") is None, "git is required for git modified navigation tests")
    def test_n_jump_keeps_near_end_hunk_visible_in_short_viewport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

            file_path = root / "pyproject.toml"
            original_lines = [f"line_{idx:03d} = {idx}\n" for idx in range(1, 61)]
            original = "".join(original_lines)
            file_path.write_text(original, encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            updated_lines = list(original_lines)
            updated_lines[56] = "line_057 = 'near-end-hunk'\n"
            updated = "".join(updated_lines)
            file_path.write_text(updated, encoding="utf-8")
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
                snapshots["path"] = state.current_path.resolve()
                snapshots["start"] = state.start
                snapshots["max_start"] = state.max_start
                snapshots["is_diff"] = state.preview_is_git_diff
                visible_rows = max(1, len(state.lines) - state.max_start)
                window_start = max(0, min(state.start, state.max_start))
                window_end = min(len(state.lines), window_start + visible_rows)
                visible_text = "\n".join(
                    ANSI_ESCAPE_RE.sub("", row)
                    for row in state.lines[window_start:window_end]
                )
                snapshots["visible_text"] = visible_text

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.source_pane.path.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.runtime.app.shutil.get_terminal_size", return_value=os.terminal_size((120, 24))
            ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.source_pane.path.sys.stdout.fileno", return_value=1
            ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ), mock.patch(
                "lazyviewer.runtime.app.GIT_STATUS_REFRESH_SECONDS", 0.0
            ):
                app_runtime.run_pager("", root, "monokai", False, False)

            self.assertEqual(snapshots["path"], file_path.resolve())
            self.assertTrue(bool(snapshots["is_diff"]))
            self.assertEqual(int(snapshots["start"]), int(snapshots["max_start"]))
            self.assertIn("near-end-hunk", str(snapshots["visible_text"]))

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
