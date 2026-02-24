"""Tests for source-pane geometry helpers."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.runtime.state import AppState
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


if __name__ == "__main__":
    unittest.main()
