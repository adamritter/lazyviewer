"""Tree-filter row, selection, and preview-highlight render tests."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.render import render_dual_page

class RenderStatusFilteringTestsPart1(unittest.TestCase):
    def test_tree_filter_renders_query_row_in_left_pane(self) -> None:
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
                tree_filter_active=True,
                tree_filter_query="main",
                tree_filter_editing=True,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("p> main", rendered)

    def test_tree_filter_query_row_shows_cursor_when_requested(self) -> None:
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
                tree_filter_active=True,
                tree_filter_query="main",
                tree_filter_editing=True,
                tree_filter_cursor_visible=True,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("p> main_", rendered)

    def test_tree_filter_query_row_supports_custom_prefix(self) -> None:
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
                tree_filter_active=True,
                tree_filter_query="hello",
                tree_filter_editing=True,
                tree_filter_cursor_visible=True,
                tree_filter_prefix="/>",
                tree_filter_placeholder="type to search content",
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("/> hello_", rendered)

    def test_source_selection_uses_truecolor_background_highlight(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["alpha beta", "second line"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=4,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=90,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                source_selection_anchor=(0, 6),
                source_selection_focus=(1, 4),
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("beta", rendered)
        self.assertIn("seco", rendered)
        self.assertGreaterEqual(rendered.count("\033[48;2;58;92;188m"), 2)

    def test_source_selection_keeps_background_after_ansi_resets(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["\033[38;5;81malpha\033[39;49;00m beta"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=3,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=90,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                source_selection_anchor=(0, 0),
                source_selection_focus=(0, 10),
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("\033[39;49;00;48;2;58;92;188m", rendered)
        self.assertIn("beta", rendered)

    def test_tree_filter_query_row_shows_match_count_and_truncated_status(self) -> None:
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
                left_width=60,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                tree_filter_active=True,
                tree_filter_query="hello",
                tree_filter_match_count=1234,
                tree_filter_truncated=True,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("1,234 matches", rendered)
        self.assertIn("truncated", rendered)

    def test_tree_filter_query_row_shows_no_results_status(self) -> None:
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
                left_width=60,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                tree_filter_active=True,
                tree_filter_query="hello",
                tree_filter_match_count=0,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("no results", rendered)
