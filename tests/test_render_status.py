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
        self.assertIn("\033[38;5;229mn/N\033[0m", rendered)
        self.assertIn("Alt+Left/Right", rendered)
        self.assertIn("m{key}", rendered)
        self.assertIn("'{key}", rendered)
        self.assertIn("command palette", rendered)
        self.assertIn("Ctrl+U", rendered)
        self.assertIn("Ctrl+D", rendered)
        self.assertIn("tree root -> parent directory", rendered)
        self.assertIn("tree root -> selected directory", rendered)


if __name__ == "__main__":
    unittest.main()
