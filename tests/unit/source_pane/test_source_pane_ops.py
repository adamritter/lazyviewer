"""Tests for source-pane geometry helpers."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.preview import DIRECTORY_GROWTH_STEP, PreviewService
from lazyviewer.render import RenderContext, RetainedPageRenderer
from lazyviewer.search import SearchService
from lazyviewer.session import LayoutState, PreviewViewState, SessionState, WorkspaceViewState
from lazyviewer.session.navigation import JumpLocation
from lazyviewer.source_pane import SourcePane
from lazyviewer.source_pane.interaction.geometry import SourcePaneGeometry
from lazyviewer.source_pane.presenter import PreviewPresenter
from lazyviewer.source_pane.preview_controller import PreviewController
from lazyviewer.tree_model import TreeEntry
from lazyviewer.tree_pane.panels.filter import TreeFilterController
from lazyviewer.tree_pane.sync import PreviewSelection
from lazyviewer.ui_theme import resolve_theme
from lazyviewer.workspace import WorkspaceService


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
    def test_tree_mouse_wheel_previews_once_for_coalesced_large_search_burst(self) -> None:
        root = Path("/tmp/lazyviewer-wheel-search").resolve()
        entries = [
            TreeEntry(
                path=root / f"result_{index:04d}.py",
                depth=1,
                is_dir=False,
                kind="search_hit",
                display=f"result {index}",
                line=index + 1,
                column=1,
            )
            for index in range(2_000)
        ]
        state = SessionState(
            workspace=WorkspaceViewState(
                current_path=entries[0].path,
                active_root=root,
                expanded={root},
                show_hidden=False,
                entries=entries,
                selected=0,
            ),
            preview=PreviewViewState(
                rendered="first",
                lines=["first"],
                search_current_line=1,
                search_current_column=1,
            ),
            layout=LayoutState(
                left_width=30,
                right_width=90,
                usable_rows=24,
                last_right_width=90,
                browser_visible=True,
            ),
        )
        state.filter.active = True
        state.filter.mode = "content"
        state.filter.query = "result"
        refresh_calls: list[dict[str, bool]] = []
        jumped_lines: list[int] = []
        selection = PreviewSelection(
            state=state,
            clear_source_selection=lambda: False,
            refresh_rendered_for_current_path=lambda **kwargs: refresh_calls.append(
                kwargs
            ),
            jump_to_line=jumped_lines.append,
        )
        controller = TreeFilterController(
            state=state,
            visible_content_rows=lambda: 20,
            rebuild_screen_lines=lambda **_kwargs: None,
            preview_selected_entry=selection.preview_selected_entry,
            current_jump_location=lambda: JumpLocation(
                path=state.workspace.current_path,
                start=state.preview.scroll,
                text_x=state.preview.horizontal_scroll,
            ),
            record_jump_if_changed=lambda _origin: None,
            jump_to_path=lambda _path: None,
            jump_to_line=lambda _line: None,
            search_service=SearchService(WorkspaceService()),
        )
        pane = SourcePane(
            state=state,
            visible_content_rows=lambda: 20,
            move_tree_selection=controller.move_tree_selection,
            maybe_grow_directory_preview=lambda: False,
            clear_source_selection=lambda: False,
            copy_selected_source_range=lambda _anchor, _focus: False,
            directory_preview_target_for_display_line=lambda _line: None,
            open_tree_filter=lambda _mode: None,
            apply_tree_filter_query=lambda _query, **_kwargs: None,
            jump_to_path=lambda _path: None,
            get_terminal_size=lambda _fallback: os.terminal_size((120, 24)),
        )
        renderer = RetainedPageRenderer()

        def render_context() -> RenderContext:
            return RenderContext(
                text_lines=state.preview.lines,
                text_start=state.preview.scroll,
                tree_entries=state.workspace.entries,
                tree_start=state.workspace.scroll,
                tree_selected=state.workspace.selected,
                max_lines=state.layout.usable_rows,
                current_path=state.workspace.current_path,
                tree_root=state.workspace.active_root,
                expanded=state.workspace.expanded,
                width=120,
                left_width=state.layout.left_width,
                text_x=state.preview.horizontal_scroll,
                wrap_text=state.preview.wrap,
                browser_visible=True,
                show_hidden=False,
                tree_filter_active=True,
                tree_filter_mode="content",
                tree_filter_query=state.filter.query,
                tree_search_query=state.filter.query,
                text_search_query=state.filter.query,
                text_search_current_line=state.preview.search_current_line,
                text_search_current_column=state.preview.search_current_column,
            )

        renderer.render(render_context())

        pane.handle_tree_mouse_wheel("MOUSE_WHEEL_DOWN:5:10:80")

        renderer.render(render_context())

        self.assertEqual(state.workspace.selected, 80)
        self.assertEqual(state.workspace.current_path, entries[80].path)
        self.assertEqual(state.preview.search_current_line, 81)
        self.assertEqual(state.preview.search_current_column, 1)
        self.assertEqual(
            refresh_calls,
            [{"reset_scroll": True, "reset_dir_budget": True}],
        )
        self.assertEqual(jumped_lines, [80])
        self.assertEqual(renderer.stats.tree_renders, 2)
        self.assertEqual(renderer.stats.preview_renders, 2)

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
