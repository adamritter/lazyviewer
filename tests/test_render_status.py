"""Rendering/status-line regression tests.

Validates help-panel row allocation and bottom status composition.
Also checks search-mode help context and modal-help rendering behavior.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.git_status import _ADDED_BG_SGR, _apply_line_background
from lazyviewer.render import (
    build_status_line,
    help_panel_row_count,
    render_dual_page,
    render_help_page,
    sticky_symbol_headers_for_position,
)


def _sticky_case(
    source: str,
    source_line: int,
    *,
    suffix: str = ".py",
    wrap_text: bool = False,
    content_rows: int = 8,
) -> list[tuple[str, str]]:
    text_lines = source.splitlines()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"case{suffix}"
        path.write_text(source, encoding="utf-8")
        sticky = sticky_symbol_headers_for_position(
            text_lines=text_lines,
            text_start=max(0, source_line - 1),
            content_rows=content_rows,
            current_path=path,
            wrap_text=wrap_text,
            preview_is_git_diff=False,
        )
    return [(entry.kind, entry.name) for entry in sticky]


class RenderStatusTests(unittest.TestCase):
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
        self.assertIn("n/N", rendered)
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

    def test_render_shows_sticky_symbol_headers_with_separator_lines(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "class Demo:\n"
            "    def run(self):\n"
            "        x = 1\n"
            "        y = 2\n"
            "        return x + y\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=3,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertRegex(rendered, r"\x1b\[4mclass Demo:")
        self.assertRegex(rendered, r"\x1b\[4m\s+def run\(self\):")
        self.assertNotIn("fn run", rendered)
        self.assertIn("─", rendered)
        self.assertIn("return x + y", rendered)

    def test_render_shows_full_nested_sticky_chain(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def run(self):\n"
            "            value = 2\n"
            "            return value\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=4,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=7,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=120,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        outer_idx = rendered.find("class Outer:")
        inner_idx = rendered.find("class Inner:")
        run_idx = rendered.find("def run(self):")
        self.assertGreaterEqual(outer_idx, 0)
        self.assertGreater(inner_idx, outer_idx)
        self.assertGreater(run_idx, inner_idx)

    def test_render_shows_sticky_headers_for_git_diff_preview(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "class Box:\n"
            "    def first(self):\n"
            "        pass\n"
            "\n"
            "    def second(self):\n"
            "        value = 2\n"
            "        return value\n"
        )
        # Simulate annotated diff preview lines where a removed line is injected.
        text_lines = [
            "class Box:",
            "    def first(self):",
            "\033[48;2;92;43;49m        removed = 0\033[K\033[0m",
            "\033[48;2;36;74;52m        pass\033[K\033[0m",
            "",
            "    def second(self):",
            "        value = 2",
            "        return value",
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=4,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=4,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=120,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                    preview_is_git_diff=True,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertRegex(rendered, r"\x1b\[4mclass Box:")
        self.assertNotRegex(rendered, r"\x1b\[4m\s+def second\(self\):")
        self.assertIn("def second(self):", rendered)

    def test_git_diff_sticky_render_avoids_quadratic_diff_mapping_calls(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source_lines = ["class Big:"]
        diff_lines = ["class Big:"]
        for idx in range(1, 351):
            source_lines.extend(
                [
                    f"    def m{idx}(self):",
                    f"        value = {idx}",
                    "        return value",
                ]
            )
            diff_lines.extend(
                [
                    f"    def m{idx}(self):",
                    "\033[48;2;92;43;49m        old_value = 0\033[K\033[0m",
                    f"\033[48;2;36;74;52m        value = {idx}\033[K\033[0m",
                    "        return value",
                ]
            )

        source = "\n".join(source_lines) + "\n"
        text_lines = diff_lines
        diff_lookup_calls = {"count": 0}

        import lazyviewer.render as render_mod

        original_source_line_raw_text = render_mod._source_line_raw_text

        def counting_source_line_raw_text(
            text_lines_arg: list[str],
            source_line: int,
            wrap_text: bool,
            preview_is_git_diff: bool = False,
        ) -> str:
            if preview_is_git_diff:
                diff_lookup_calls["count"] += 1
            return original_source_line_raw_text(
                text_lines_arg,
                source_line,
                wrap_text,
                preview_is_git_diff=preview_is_git_diff,
            )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render._source_line_raw_text", side_effect=counting_source_line_raw_text), mock.patch(
                "lazyviewer.render.os.write", side_effect=capture
            ):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=max(0, len(text_lines) - 6),
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=6,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=140,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                    preview_is_git_diff=True,
                )

        # Repeated diff-source remapping used to spike into hundreds/thousands of calls per frame.
        self.assertLess(diff_lookup_calls["count"], 80)

    def test_render_scroll_by_one_keeps_lower_content_progressing_by_one_with_sticky(self) -> None:
        source = (
            "def run():\n"
            "    first = 1\n"
            "    second = 2\n"
            "    third = 3\n"
            "    fourth = 4\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")

            writes_top: list[bytes] = []
            writes_scrolled_1: list[bytes] = []
            writes_scrolled_2: list[bytes] = []

            def capture_top(_fd: int, data: bytes) -> int:
                writes_top.append(data)
                return len(data)

            def capture_scrolled_1(_fd: int, data: bytes) -> int:
                writes_scrolled_1.append(data)
                return len(data)

            def capture_scrolled_2(_fd: int, data: bytes) -> int:
                writes_scrolled_2.append(data)
                return len(data)

            with mock.patch("lazyviewer.render.os.write", side_effect=capture_top):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=0,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=3,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

            with mock.patch("lazyviewer.render.os.write", side_effect=capture_scrolled_1):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=1,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=3,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

            with mock.patch("lazyviewer.render.os.write", side_effect=capture_scrolled_2):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=3,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        top_rendered = b"".join(writes_top).decode("utf-8", errors="replace")
        scrolled_1_rendered = b"".join(writes_scrolled_1).decode("utf-8", errors="replace")
        scrolled_2_rendered = b"".join(writes_scrolled_2).decode("utf-8", errors="replace")

        self.assertIn("def run():", top_rendered)
        self.assertIn("first = 1", top_rendered)
        self.assertIn("second = 2", top_rendered)

        self.assertRegex(scrolled_1_rendered, r"\x1b\[4mdef run\(\):")
        self.assertIn("second = 2", scrolled_1_rendered)
        self.assertIn("third = 3", scrolled_1_rendered)
        self.assertNotIn("first = 1", scrolled_1_rendered)

        self.assertRegex(scrolled_2_rendered, r"\x1b\[4mdef run\(\):")
        self.assertIn("third = 3", scrolled_2_rendered)
        self.assertIn("fourth = 4", scrolled_2_rendered)
        self.assertNotIn("second = 2", scrolled_2_rendered)

    def test_render_nested_sticky_headers_transition_smoothly_for_class_and_method(self) -> None:
        source = (
            "class Box:\n"
            "    def run(self):\n"
            "        first = 1\n"
            "        second = 2\n"
            "        third = 3\n"
            "        fourth = 4\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")

            writes_scrolled_1: list[bytes] = []
            writes_scrolled_2: list[bytes] = []

            with mock.patch(
                "lazyviewer.render.os.write",
                side_effect=lambda _fd, data: writes_scrolled_1.append(data) or len(data),
            ):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=1,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=4,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

            with mock.patch(
                "lazyviewer.render.os.write",
                side_effect=lambda _fd, data: writes_scrolled_2.append(data) or len(data),
            ):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=4,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        scrolled_1_rendered = b"".join(writes_scrolled_1).decode("utf-8", errors="replace")
        scrolled_2_rendered = b"".join(writes_scrolled_2).decode("utf-8", errors="replace")

        self.assertRegex(scrolled_1_rendered, r"\x1b\[4mclass Box:")
        self.assertRegex(scrolled_1_rendered, r"\x1b\[4m\s+def run\(self\):")
        self.assertIn("second = 2", scrolled_1_rendered)
        self.assertIn("third = 3", scrolled_1_rendered)
        self.assertNotIn("first = 1", scrolled_1_rendered)

        self.assertRegex(scrolled_2_rendered, r"\x1b\[4mclass Box:")
        self.assertRegex(scrolled_2_rendered, r"\x1b\[4m\s+def run\(self\):")
        self.assertIn("third = 3", scrolled_2_rendered)
        self.assertIn("fourth = 4", scrolled_2_rendered)
        self.assertNotIn("second = 2", scrolled_2_rendered)

    def test_render_keeps_sticky_header_on_blank_line_inside_function(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "def run():\n"
            "    first = 1\n"
            "\n"
            "    second = 2\n"
            "    return first + second\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=5,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("def run():", rendered)
        self.assertIn("─", rendered)
        self.assertIn("second = 2", rendered)

    def test_render_does_not_show_sticky_header_on_blank_separator_line(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "def first():\n"
            "    return 1\n"
            "\n"
            "def second():\n"
            "    return 2\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=5,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertNotIn("def first():", rendered)
        self.assertNotIn("─", rendered)
        self.assertIn("def second():", rendered)

    def test_sticky_case_helper_drops_finished_function_before_top_level_for(self) -> None:
        source = (
            "def run():\n"
            "    value = 1\n"
            "\n"
            "for item in items:\n"
            "    print(item)\n"
        )
        self.assertEqual(_sticky_case(source, 4), [])
        self.assertEqual(_sticky_case(source, 5), [])

    def test_sticky_case_helper_shows_function_on_first_body_line(self) -> None:
        source = (
            "def run():\n"
            "    first = 1\n"
            "    second = 2\n"
        )
        self.assertEqual(_sticky_case(source, 2), [("fn", "run")])

    def test_sticky_case_helper_keeps_outer_class_when_previous_method_ends(self) -> None:
        source = (
            "class Box:\n"
            "    def first(self):\n"
            "        return 1\n"
            "\n"
            "    def second(self):\n"
            "        value = 2\n"
            "        return value\n"
        )
        self.assertEqual(_sticky_case(source, 5), [("class", "Box"), ("fn", "second")])
        self.assertEqual(_sticky_case(source, 6), [("class", "Box"), ("fn", "second")])
        self.assertEqual(_sticky_case(source, 7), [("class", "Box"), ("fn", "second")])

    def test_sticky_case_helper_handles_closing_brace_transition(self) -> None:
        source = (
            "function run() {\n"
            "  if (value) {\n"
            "    use(value);\n"
            "  }\n"
            "}\n"
            "\n"
            "for (const item of items) {\n"
            "  use(item);\n"
            "}\n"
        )
        self.assertEqual(_sticky_case(source, 5, suffix=".js"), [("fn", "run")])
        self.assertEqual(_sticky_case(source, 6, suffix=".js"), [])
        self.assertEqual(_sticky_case(source, 7, suffix=".js"), [])

    def test_sticky_case_helper_drops_function_before_decorator_and_next_definition(self) -> None:
        source = (
            "def first():\n"
            "    return 1\n"
            "\n"
            "@decorator\n"
            "def second():\n"
            "    return 2\n"
        )
        self.assertEqual(_sticky_case(source, 4), [])
        self.assertEqual(_sticky_case(source, 5), [])

    def test_sticky_case_helper_drops_function_before_top_level_comment_and_loop(self) -> None:
        source = (
            "def first():\n"
            "    return 1\n"
            "\n"
            "# outside the function now\n"
            "for value in values:\n"
            "    print(value)\n"
        )
        self.assertEqual(_sticky_case(source, 4), [])
        self.assertEqual(_sticky_case(source, 6), [])

    def test_render_sticky_headers_preserve_source_highlighting(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "def run():\n"
            "    return 1\n"
            "    return 2\n"
        )
        text_lines = [
            "\033[1;34mdef\033[39;49;00m run():",
            "    return 1",
            "    return 2",
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=4,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("\033[39;49;00;4m run():", rendered)

    def test_render_sticky_headers_work_for_javascript_without_tree_sitter(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "class Box {\n"
            "  constructor() {}\n"
            "}\n"
            "\n"
            "function boot() {\n"
            "  const value = 3;\n"
            "  return value;\n"
            "}\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.js"
            path.write_text(source, encoding="utf-8")
            with (
                mock.patch(
                    "lazyviewer.symbols._load_parser",
                    return_value=(None, "Tree-sitter parser package not found. Install tree-sitter-languages."),
                ),
                mock.patch("lazyviewer.render.os.write", side_effect=capture),
            ):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=5,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=6,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("function boot() {", rendered)
        self.assertNotIn("fn boot", rendered)
        self.assertNotIn("class Box", rendered)
        self.assertIn("return value;", rendered)


if __name__ == "__main__":
    unittest.main()
