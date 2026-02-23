"""Core rendering/status-line regression tests."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.preview.diff import _ADDED_BG_SGR, _apply_line_background
from lazyviewer.render import (
    build_status_line,
    help_panel_row_count,
    render_dual_page,
)


class RenderStatusCoreTests(unittest.TestCase):
    def test_help_panel_row_count_leaves_content_space(self) -> None:
        self.assertEqual(help_panel_row_count(10, show_help=False), 0)
        self.assertEqual(help_panel_row_count(1, show_help=True), 0)
        self.assertGreaterEqual(help_panel_row_count(6, show_help=True), 1)

    def test_help_panel_row_count_uses_text_only_rows_when_browser_hidden(self) -> None:
        with (
            mock.patch("lazyviewer.render.help.HELP_PANEL_TREE_LINES", ("tree-1", "tree-2", "tree-3")),
            mock.patch("lazyviewer.render.help.HELP_PANEL_TEXT_LINES", ("text-1", "text-2")),
            mock.patch("lazyviewer.render.help.HELP_PANEL_TEXT_ONLY_LINES", ("text-only-1",)),
        ):
            self.assertEqual(
                help_panel_row_count(
                    20,
                    show_help=True,
                    browser_visible=False,
                ),
                1,
            )

    def test_build_status_line_places_help_on_right(self) -> None:
        line = build_status_line("file.py (1-10/200  4.5%)", width=60)
        self.assertEqual(len(line), 59)
        self.assertIn(" │ ? Help", line)
        self.assertTrue(line.endswith("│ ? Help"))

    def test_render_status_ends_with_help_suffix(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["line 1", "line 2"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=3,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=80,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        matches = re.findall(r"\x1b\[7m([^\x1b]*)\x1b\[0m", rendered)
        self.assertTrue(matches)
        self.assertTrue(matches[-1].endswith("│ ? Help"))

    def test_render_status_includes_transient_status_message(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["line 1", "line 2"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=3,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=120,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                status_message="wrapped to first change",
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        matches = re.findall(r"\x1b\[7m([^\x1b]*)\x1b\[0m", rendered)
        self.assertTrue(matches)
        self.assertIn("wrapped to first change", matches[-1])

    def test_git_diff_background_persists_when_horizontal_scroll_reaches_line_end(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        diff_line = _apply_line_background("abcdef", _ADDED_BG_SGR)
        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=[diff_line],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=2,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=60,
                left_width=24,
                text_x=6,
                wrap_text=False,
                browser_visible=False,
                show_hidden=False,
                preview_is_git_diff=True,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        first_line = rendered.split("\r\n")[0]
        self.assertIn("\033[48;2;36;74;52m\033[K", first_line)

    def test_bottom_help_panel_renders_without_replacing_main_view(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["line 1", "line 2", "line 3", "line 4"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=6,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=100,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=False,
                show_hidden=False,
                show_help=True,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("line 1", rendered)
        self.assertIn("KEYS", rendered)
        self.assertIn("Ctrl+P", rendered)
        self.assertIn("\033[38;5;229mCtrl+P\033[0m", rendered)

    def test_bottom_help_panel_uses_left_only_query_context_while_editing(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["line 1", "line 2", "line 3", "line 4"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=12,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=120,
                left_width=40,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                show_help=True,
                tree_filter_active=True,
                tree_filter_mode="content",
                tree_filter_editing=True,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("SEARCH QUERY", rendered)
        self.assertIn("Ctrl+J/K", rendered)
        self.assertNotIn("SEARCH HITS + TEXT", rendered)

    def test_bottom_help_panel_uses_search_hits_context_after_enter(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["line 1", "line 2", "line 3", "line 4"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=12,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=120,
                left_width=40,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                show_help=True,
                tree_filter_active=True,
                tree_filter_mode="content",
                tree_filter_editing=False,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("SEARCH HITS + TEXT", rendered)
        self.assertIn("Ctrl+P", rendered)
        self.assertIn("n/N/p", rendered)
        self.assertNotIn("SEARCH QUERY", rendered)

    def test_bottom_help_panel_handles_mismatched_line_counts(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        tree_help = ("TREE", "tree-2", "tree-3")
        text_help = ("TEXT",)
        text_only_help = ("TEXT-ONLY",)

        with (
            mock.patch("lazyviewer.render.help.HELP_PANEL_TREE_LINES", tree_help),
            mock.patch("lazyviewer.render.help.HELP_PANEL_TEXT_LINES", text_help),
            mock.patch("lazyviewer.render.help.HELP_PANEL_TEXT_ONLY_LINES", text_only_help),
            mock.patch("lazyviewer.render.os.write", side_effect=capture),
        ):
            render_dual_page(
                text_lines=["line 1", "line 2", "line 3"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=5,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=100,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=False,
                show_hidden=False,
                show_help=True,
            )

            render_dual_page(
                text_lines=["line 1", "line 2", "line 3"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=5,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=100,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                show_help=True,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("TEXT-ONLY", rendered)
        self.assertIn("TREE", rendered)
        self.assertIn("TEXT", rendered)
