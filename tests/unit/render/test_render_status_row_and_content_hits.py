"""Tree-filter row, selection, and preview-highlight render tests."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.render import render_dual_page

class RenderStatusFilteringTestsPart2(unittest.TestCase):
    def test_tree_filter_query_row_shows_loading_spinner_status(self) -> None:
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
                tree_filter_loading=True,
                tree_filter_spinner_frame=1,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("/ searching", rendered)

    def test_status_percent_uses_scroll_range_top_and_bottom(self) -> None:
        top_writes: list[bytes] = []
        bottom_writes: list[bytes] = []
        text_lines = [f"line {idx}" for idx in range(1, 21)]

        def capture_top(_fd: int, data: bytes) -> int:
            top_writes.append(data)
            return len(data)

        def capture_bottom(_fd: int, data: bytes) -> int:
            bottom_writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture_top):
            render_dual_page(
                text_lines=text_lines,
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=6,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=120,
                left_width=40,
                text_x=0,
                wrap_text=False,
                browser_visible=False,
                show_hidden=False,
            )

        with mock.patch("lazyviewer.render.os.write", side_effect=capture_bottom):
            render_dual_page(
                text_lines=text_lines,
                text_start=14,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=6,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=120,
                left_width=40,
                text_x=0,
                wrap_text=False,
                browser_visible=False,
                show_hidden=False,
            )

        top_rendered = b"".join(top_writes).decode("utf-8", errors="replace")
        bottom_rendered = b"".join(bottom_writes).decode("utf-8", errors="replace")
        top_status = re.findall(r"\x1b\[7m([^\x1b]*)\x1b\[0m", top_rendered)[-1]
        bottom_status = re.findall(r"\x1b\[7m([^\x1b]*)\x1b\[0m", bottom_rendered)[-1]
        self.assertIn("  0.0%)", top_status)
        self.assertIn("100.0%)", bottom_status)

    def test_status_line_numbers_stay_in_source_space_when_wrapped(self) -> None:
        writes: list[bytes] = []
        text_lines = [
            "alpha-part-1",
            "alpha-part-2\n",
            "beta\n",
            "gamma\n",
        ]

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=text_lines,
                text_start=1,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=2,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=100,
                left_width=40,
                text_x=0,
                wrap_text=True,
                browser_visible=False,
                show_hidden=False,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        status = re.findall(r"\x1b\[7m([^\x1b]*)\x1b\[0m", rendered)[-1]
        self.assertIn("(1-2/3", status)

    def test_command_picker_query_row_uses_command_prefix(self) -> None:
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
                max_lines=4,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=80,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                picker_active=True,
                picker_mode="commands",
                picker_query="wrap",
                picker_items=["Toggle wrap (w)"],
                picker_selected=0,
                picker_focus="query",
                picker_list_start=0,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn(": wrap", rendered)
        self.assertIn("Toggle wrap (w)", rendered)

    def test_right_preview_highlights_content_search_matches(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["alpha beta gamma"],
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
                text_search_query="beta",
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("\033[7;1mbeta\033[27;22m", rendered)

    def test_right_preview_highlight_handles_ansi_colored_text(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["\033[38;5;81malpha beta\033[0m"],
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
                text_search_query="beta",
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("\033[7;1m", rendered)
        self.assertIn("beta", rendered)

    def test_right_preview_distinguishes_current_content_hit_from_other_matches(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["beta beta"],
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
                text_search_query="beta",
                text_search_current_line=1,
                text_search_current_column=6,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("\033[1mbeta\033[22m \033[7;1mbeta\033[27;22m", rendered)
