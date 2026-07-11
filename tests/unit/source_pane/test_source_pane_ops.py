"""Tests for source-pane geometry helpers."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.preview import DIRECTORY_GROWTH_STEP, PreviewService
from lazyviewer.session import LayoutState, PreviewViewState, SessionState, WorkspaceViewState
from lazyviewer.source_pane.interaction.geometry import SourcePaneGeometry
from lazyviewer.source_pane.presenter import PreviewPresenter
from lazyviewer.source_pane.preview_controller import PreviewController
from lazyviewer.tree_model import TreeEntry
from lazyviewer.ui_theme import resolve_theme


def _make_state(lines: list[str]) -> SessionState:
    root = Path("/tmp").resolve()
    return SessionState(
        workspace=WorkspaceViewState(
            current_path=root, active_root=root, expanded={root}, show_hidden=False,
            entries=[TreeEntry(path=root, depth=0, is_dir=True)], selected=0,
        ),
        preview=PreviewViewState(rendered="\n".join(lines), lines=lines),
        layout=LayoutState(
            left_width=24, right_width=80, usable_rows=24,
            last_right_width=80, browser_visible=False,
        ),
    )


def _controller(state: SessionState, visible_rows: int = 20) -> PreviewController:
    return PreviewController(
        state=state,
        service=PreviewService(),
        presenter=PreviewPresenter(
            style="monokai",
            color=False,
            theme=resolve_theme(None, no_color=True),
        ),
        rebuild_screen_lines=lambda **_kwargs: None,
        visible_content_rows=lambda: visible_rows,
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
            state.preview.lines = state.preview.lines + [("y" * 220)]
            updated = ops.max_horizontal_text_offset()

        self.assertGreater(updated, 0)
        self.assertEqual(width_mock.call_count, len(lines) + len(state.preview.lines))

    def test_initial_directory_preview_max_entries_is_viewport_bounded(self) -> None:
        self.assertEqual(PreviewController.initial_max_entries(30), 26)
        self.assertEqual(PreviewController.initial_max_entries(4), 1)

    def test_directory_preview_growth_step_is_viewport_bounded(self) -> None:
        self.assertEqual(PreviewController.growth_step(1), 16)
        self.assertEqual(PreviewController.growth_step(25), 50)
        self.assertEqual(
            PreviewController.growth_step(10_000),
            DIRECTORY_GROWTH_STEP,
        )

    def test_maybe_grow_directory_preview_uses_adaptive_growth_target(self) -> None:
        state = _make_state([f"line_{idx}" for idx in range(20)])
        state.workspace.current_path = state.workspace.active_root
        state.preview.directory_path = state.workspace.active_root
        state.preview.directory_truncated = True
        state.preview.directory_max_entries = 25
        state.preview.scroll = 10
        state.preview.max_scroll = 10
        refresh_calls: list[dict[str, object]] = []

        def refresh_rendered(**kwargs) -> None:
            refresh_calls.append(kwargs)
            state.preview.lines = state.preview.lines + [f"extra_{idx}" for idx in range(5)]
            state.preview.max_scroll = 15

        controller = _controller(state)
        with mock.patch.object(controller, "refresh", side_effect=refresh_rendered):
            grew = controller.maybe_grow()

        self.assertTrue(grew)
        self.assertEqual(state.preview.directory_max_entries, 70)
        self.assertEqual(
            refresh_calls,
            [{"reset_scroll": False, "reset_dir_budget": False}],
        )

    def test_directory_prefetch_target_entries_returns_none_when_not_needed(self) -> None:
        state = _make_state([f"line_{idx}" for idx in range(100)])
        state.workspace.current_path = state.workspace.active_root
        state.preview.directory_path = state.workspace.active_root
        state.preview.directory_truncated = True
        state.preview.directory_max_entries = 250
        state.preview.scroll = 0
        state.preview.max_scroll = 100

        target = _controller(state).prefetch_target_entries()
        self.assertIsNone(target)

    def test_directory_prefetch_target_entries_returns_next_budget_when_needed(self) -> None:
        state = _make_state([f"line_{idx}" for idx in range(30)])
        state.workspace.current_path = state.workspace.active_root
        state.preview.directory_path = state.workspace.active_root
        state.preview.directory_truncated = True
        state.preview.directory_max_entries = 25
        state.preview.scroll = 0
        state.preview.max_scroll = 4

        target = _controller(state).prefetch_target_entries()
        self.assertEqual(target, 240)

if __name__ == "__main__":
    unittest.main()
