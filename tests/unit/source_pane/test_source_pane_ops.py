"""Tests for source-pane geometry helpers."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.runtime.state import AppState
from lazyviewer.source_pane import SourcePane
from lazyviewer.source_pane.interaction.geometry import SourcePaneGeometry
from lazyviewer.tree_model import TreeEntry


def _make_state(lines: list[str]) -> AppState:
    root = Path("/tmp").resolve()
    return AppState(
        current_path=root,
        tree_root=root,
        expanded={root},
        show_hidden=False,
        tree_entries=[TreeEntry(path=root, depth=0, is_dir=True)],
        selected_idx=0,
        rendered="\n".join(lines),
        lines=lines,
        start=0,
        tree_start=0,
        text_x=0,
        wrap_text=False,
        left_width=24,
        right_width=80,
        usable=24,
        max_start=0,
        last_right_width=80,
        browser_visible=False,
    )


class SourcePaneGeometryTests(unittest.TestCase):
    def test_max_horizontal_text_offset_caches_line_width_scan(self) -> None:
        lines = [f"line_{idx:04d}" + ("x" * 120) for idx in range(200)]
        state = _make_state(lines)
        ops = SourcePaneGeometry(
            state=state,
            visible_content_rows=lambda: 20,
            get_terminal_size=lambda _fallback: os.terminal_size((120, 24)),
        )

        with mock.patch(
            "lazyviewer.source_pane.interaction.geometry._rendered_line_display_width",
            side_effect=lambda line: len(line),
        ) as width_mock:
            first = ops.max_horizontal_text_offset()
            second = ops.max_horizontal_text_offset()

        self.assertEqual(first, second)
        self.assertEqual(width_mock.call_count, len(lines))

    def test_max_horizontal_text_offset_recomputes_after_lines_object_changes(self) -> None:
        lines = [f"line_{idx:04d}" + ("x" * 80) for idx in range(50)]
        state = _make_state(lines)
        ops = SourcePaneGeometry(
            state=state,
            visible_content_rows=lambda: 20,
            get_terminal_size=lambda _fallback: os.terminal_size((100, 24)),
        )

        with mock.patch(
            "lazyviewer.source_pane.interaction.geometry._rendered_line_display_width",
            side_effect=lambda line: len(line),
        ) as width_mock:
            ops.max_horizontal_text_offset()
            state.lines = state.lines + [("y" * 220)]
            updated = ops.max_horizontal_text_offset()

        self.assertGreater(updated, 0)
        self.assertEqual(width_mock.call_count, len(lines) + len(state.lines))

    def test_initial_directory_preview_max_entries_is_viewport_bounded(self) -> None:
        self.assertEqual(SourcePane.initial_directory_preview_max_entries(30), 26)
        self.assertEqual(SourcePane.initial_directory_preview_max_entries(4), 1)

    def test_directory_preview_growth_step_is_viewport_bounded(self) -> None:
        self.assertEqual(SourcePane.directory_preview_growth_step(1), 16)
        self.assertEqual(SourcePane.directory_preview_growth_step(25), 50)
        self.assertEqual(
            SourcePane.directory_preview_growth_step(10_000),
            SourcePane.DIR_PREVIEW_GROWTH_STEP,
        )

    def test_maybe_grow_directory_preview_uses_adaptive_growth_target(self) -> None:
        state = _make_state([f"line_{idx}" for idx in range(20)])
        state.current_path = state.tree_root
        state.dir_preview_path = state.tree_root
        state.dir_preview_truncated = True
        state.dir_preview_max_entries = 25
        state.start = 10
        state.max_start = 10
        refresh_calls: list[dict[str, object]] = []

        def refresh_rendered(**kwargs) -> None:
            refresh_calls.append(kwargs)
            state.lines = state.lines + [f"extra_{idx}" for idx in range(5)]
            state.max_start = 15

        grew = SourcePane.maybe_grow_directory_preview(
            state,
            visible_content_rows=lambda: 20,
            refresh_rendered_for_current_path_fn=refresh_rendered,
        )

        self.assertTrue(grew)
        self.assertEqual(state.dir_preview_max_entries, 70)
        self.assertEqual(
            refresh_calls,
            [{"reset_scroll": False, "reset_dir_budget": False}],
        )

    def test_maybe_prefetch_directory_preview_grows_when_headroom_is_low(self) -> None:
        state = _make_state([f"line_{idx}" for idx in range(30)])
        state.current_path = state.tree_root
        state.dir_preview_path = state.tree_root
        state.dir_preview_truncated = True
        state.dir_preview_max_entries = 25
        state.start = 0
        state.max_start = 4
        refresh_calls: list[dict[str, object]] = []

        def refresh_rendered(**kwargs) -> None:
            refresh_calls.append(kwargs)
            state.lines = state.lines + [f"extra_{idx}" for idx in range(20)]
            state.max_start = 24

        prefetched = SourcePane.maybe_prefetch_directory_preview(
            state,
            visible_content_rows=lambda: 20,
            refresh_rendered_for_current_path_fn=refresh_rendered,
        )

        self.assertTrue(prefetched)
        self.assertEqual(state.dir_preview_max_entries, 240)
        self.assertEqual(
            refresh_calls,
            [{"reset_scroll": False, "reset_dir_budget": False}],
        )

    def test_directory_prefetch_target_entries_returns_none_when_not_needed(self) -> None:
        state = _make_state([f"line_{idx}" for idx in range(100)])
        state.current_path = state.tree_root
        state.dir_preview_path = state.tree_root
        state.dir_preview_truncated = True
        state.dir_preview_max_entries = 250
        state.start = 0
        state.max_start = 100

        target = SourcePane.directory_prefetch_target_entries(
            state,
            visible_content_rows=lambda: 20,
        )
        self.assertIsNone(target)

    def test_directory_prefetch_target_entries_returns_next_budget_when_needed(self) -> None:
        state = _make_state([f"line_{idx}" for idx in range(30)])
        state.current_path = state.tree_root
        state.dir_preview_path = state.tree_root
        state.dir_preview_truncated = True
        state.dir_preview_max_entries = 25
        state.start = 0
        state.max_start = 4

        target = SourcePane.directory_prefetch_target_entries(
            state,
            visible_content_rows=lambda: 20,
        )
        self.assertEqual(target, 240)

    def test_maybe_prefetch_directory_preview_skips_when_headroom_is_sufficient(self) -> None:
        state = _make_state([f"line_{idx}" for idx in range(100)])
        state.current_path = state.tree_root
        state.dir_preview_path = state.tree_root
        state.dir_preview_truncated = True
        state.dir_preview_max_entries = 250
        state.start = 0
        state.max_start = 100
        refresh_calls: list[dict[str, object]] = []

        prefetched = SourcePane.maybe_prefetch_directory_preview(
            state,
            visible_content_rows=lambda: 20,
            refresh_rendered_for_current_path_fn=lambda **kwargs: refresh_calls.append(kwargs),
        )

        self.assertFalse(prefetched)
        self.assertEqual(state.dir_preview_max_entries, 250)
        self.assertEqual(refresh_calls, [])


if __name__ == "__main__":
    unittest.main()
