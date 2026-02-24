"""Tests for runtime navigation helpers.

Focuses on wrap/unwrapped line mapping and top-line preservation.
Also validates named-mark persistence hooks.
"""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest import mock

from lazyviewer.render.ansi import build_screen_lines
from lazyviewer.tree_pane.panels.picker import (
    NavigationPickerDeps,
    NavigationPickerOps,
    _first_display_index_for_source_line,
    _source_line_for_display_index,
)
from lazyviewer.runtime.state import AppState
from lazyviewer.tree_model import TreeEntry


def _make_state(*, wrap_text: bool, rendered: str, visible_rows: int, width: int) -> AppState:
    root = Path("/tmp").resolve()
    lines = build_screen_lines(rendered, width, wrap=wrap_text)
    max_start = max(0, len(lines) - visible_rows)
    return AppState(
        current_path=root / "demo.py",
        tree_root=root,
        expanded={root},
        show_hidden=False,
        tree_entries=[TreeEntry(path=root, depth=0, is_dir=True)],
        selected_idx=0,
        rendered=rendered,
        lines=lines,
        start=0,
        tree_start=0,
        text_x=0,
        wrap_text=wrap_text,
        left_width=24,
        right_width=80,
        usable=24,
        max_start=max_start,
        last_right_width=80,
    )


class RuntimeNavigationWrapTests(unittest.TestCase):
    def test_source_line_for_display_index_counts_wrapped_chunks(self) -> None:
        lines = ["abc", "def\n", "ghi", "jkl\n", "tail"]
        self.assertEqual(_source_line_for_display_index(lines, 0), 1)
        self.assertEqual(_source_line_for_display_index(lines, 1), 1)
        self.assertEqual(_source_line_for_display_index(lines, 2), 2)
        self.assertEqual(_source_line_for_display_index(lines, 4), 3)

    def test_first_display_index_for_source_line_finds_first_chunk(self) -> None:
        lines = ["abc", "def\n", "ghi", "jkl\n", "tail"]
        self.assertEqual(_first_display_index_for_source_line(lines, 1), 0)
        self.assertEqual(_first_display_index_for_source_line(lines, 2), 2)
        self.assertEqual(_first_display_index_for_source_line(lines, 3), 4)
        self.assertEqual(_first_display_index_for_source_line(lines, 99), 4)

    def test_toggle_wrap_mode_keeps_same_top_source_line_when_enabling_wrap(self) -> None:
        visible_rows = 1
        width = 3
        rendered = "abcdef\nghij\n"
        state = _make_state(wrap_text=False, rendered=rendered, visible_rows=visible_rows, width=width)
        state.start = 1  # source line 2 at top in unwrapped mode

        def rebuild_screen_lines(*, columns=None, preserve_scroll: bool = True) -> None:
            del columns
            state.lines = build_screen_lines(state.rendered, width, wrap=state.wrap_text)
            state.max_start = max(0, len(state.lines) - visible_rows)
            if preserve_scroll:
                state.start = max(0, min(state.start, state.max_start))
            else:
                state.start = 0
            if state.wrap_text:
                state.text_x = 0

        ops = NavigationPickerOps(
            NavigationPickerDeps(
                state=state,
                command_palette_items=(),
                rebuild_screen_lines=rebuild_screen_lines,
                rebuild_tree_entries=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                schedule_tree_filter_index_warmup=lambda: None,
                mark_tree_watch_dirty=lambda: None,
                reset_git_watch_context=lambda: None,
                refresh_git_status_overlay=lambda **_kwargs: None,
                visible_content_rows=lambda: visible_rows,
                refresh_rendered_for_current_path=lambda **_kwargs: None,
            )
        )

        ops.toggle_wrap_mode()

        self.assertTrue(state.wrap_text)
        self.assertEqual(state.start, 2)  # first wrapped chunk for source line 2

    def test_toggle_wrap_mode_keeps_same_top_source_line_when_disabling_wrap(self) -> None:
        visible_rows = 1
        width = 3
        rendered = "abcdef\nghij\n"
        state = _make_state(wrap_text=True, rendered=rendered, visible_rows=visible_rows, width=width)
        state.start = 3  # second chunk of source line 2 in wrapped mode

        def rebuild_screen_lines(*, columns=None, preserve_scroll: bool = True) -> None:
            del columns
            state.lines = build_screen_lines(state.rendered, width, wrap=state.wrap_text)
            state.max_start = max(0, len(state.lines) - visible_rows)
            if preserve_scroll:
                state.start = max(0, min(state.start, state.max_start))
            else:
                state.start = 0
            if state.wrap_text:
                state.text_x = 0

        ops = NavigationPickerOps(
            NavigationPickerDeps(
                state=state,
                command_palette_items=(),
                rebuild_screen_lines=rebuild_screen_lines,
                rebuild_tree_entries=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                schedule_tree_filter_index_warmup=lambda: None,
                mark_tree_watch_dirty=lambda: None,
                reset_git_watch_context=lambda: None,
                refresh_git_status_overlay=lambda **_kwargs: None,
                visible_content_rows=lambda: visible_rows,
                refresh_rendered_for_current_path=lambda **_kwargs: None,
            )
        )

        ops.toggle_wrap_mode()

        self.assertFalse(state.wrap_text)
        self.assertEqual(state.start, 1)  # source line 2 in unwrapped mode

    def test_set_named_mark_persists_marks(self) -> None:
        visible_rows = 8
        width = 80
        rendered = "first\nsecond\n"
        state = _make_state(wrap_text=False, rendered=rendered, visible_rows=visible_rows, width=width)
        state.start = 5
        state.text_x = 2

        ops = NavigationPickerOps(
            NavigationPickerDeps(
                state=state,
                command_palette_items=(),
                rebuild_screen_lines=lambda **_kwargs: None,
                rebuild_tree_entries=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                schedule_tree_filter_index_warmup=lambda: None,
                mark_tree_watch_dirty=lambda: None,
                reset_git_watch_context=lambda: None,
                refresh_git_status_overlay=lambda **_kwargs: None,
                visible_content_rows=lambda: visible_rows,
                refresh_rendered_for_current_path=lambda **_kwargs: None,
            )
        )

        with mock.patch("lazyviewer.tree_pane.panels.picker.navigation.save_named_marks") as save_named_marks:
            self.assertTrue(ops.set_named_mark("a"))

        self.assertIn("a", state.named_marks)
        self.assertEqual(state.named_marks["a"].start, 5)
        self.assertEqual(state.named_marks["a"].text_x, 2)
        save_named_marks.assert_called_once_with(state.named_marks)


if __name__ == "__main__":
    unittest.main()
