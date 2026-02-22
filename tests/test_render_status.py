from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.render import build_status_line, help_panel_row_count, render_dual_page, render_help_page


class RenderStatusTests(unittest.TestCase):
    def test_help_panel_row_count_leaves_content_space(self) -> None:
        self.assertEqual(help_panel_row_count(10, show_help=False), 0)
        self.assertEqual(help_panel_row_count(1, show_help=True), 0)
        self.assertGreaterEqual(help_panel_row_count(6, show_help=True), 1)

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

    def test_bottom_help_panel_splits_tree_and_text_sections_when_browser_visible(self) -> None:
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
                max_lines=7,
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
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("TREE", rendered)
        self.assertIn("TEXT + EXTRAS", rendered)
        self.assertIn("\033[38;5;229mh/j/k/l\033[0m", rendered)
        self.assertIn("\033[38;5;229mr\033[0m", rendered)

    def test_help_modal_mentions_reroot_key(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_help_page(width=120, height=32)

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("\033[38;5;229mr\033[0m", rendered)
        self.assertIn("\033[38;5;229mR\033[0m", rendered)
        self.assertIn("Ctrl+U", rendered)
        self.assertIn("Ctrl+D", rendered)
        self.assertIn("tree root -> parent directory", rendered)
        self.assertIn("tree root -> selected directory", rendered)


if __name__ == "__main__":
    unittest.main()
