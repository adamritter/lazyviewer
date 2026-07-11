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
    NavigationController,
    _first_display_index_for_source_line,
    _source_line_for_display_index,
)
from lazyviewer.session import LayoutState, PreviewViewState, SessionState, WorkspaceViewState
from lazyviewer.tree_model import TreeEntry


def _make_state(*, wrap_text: bool, rendered: str, visible_rows: int, width: int) -> SessionState:
    root = Path("/tmp").resolve()
    lines = build_screen_lines(rendered, width, wrap=wrap_text)
    max_start = max(0, len(lines) - visible_rows)
    return SessionState(
        workspace=WorkspaceViewState(
            current_path=root / "demo.py", active_root=root, expanded={root},
            show_hidden=False, entries=[TreeEntry(path=root, depth=0, is_dir=True)], selected=0,
        ),
        preview=PreviewViewState(rendered=rendered, lines=lines, wrap=wrap_text, max_scroll=max_start),
        layout=LayoutState(left_width=24, right_width=80, usable_rows=24, last_right_width=80),
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
        state.preview.scroll = 1  # source line 2 at top in unwrapped mode

        def rebuild_screen_lines(*, columns=None, preserve_scroll: bool = True) -> None:
            del columns
            state.preview.lines = build_screen_lines(state.preview.rendered, width, wrap=state.preview.wrap)
            state.preview.max_scroll = max(0, len(state.preview.lines) - visible_rows)
            if preserve_scroll:
                state.preview.scroll = max(0, min(state.preview.scroll, state.preview.max_scroll))
            else:
                state.preview.scroll = 0
            if state.preview.wrap:
                state.preview.horizontal_scroll = 0

        ops = NavigationController(
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
            open_tree_filter=lambda _mode: None,
        )

        ops.toggle_wrap_mode()

        self.assertTrue(state.preview.wrap)
        self.assertEqual(state.preview.scroll, 2)  # first wrapped chunk for source line 2

    def test_toggle_wrap_mode_keeps_same_top_source_line_when_disabling_wrap(self) -> None:
        visible_rows = 1
        width = 3
        rendered = "abcdef\nghij\n"
        state = _make_state(wrap_text=True, rendered=rendered, visible_rows=visible_rows, width=width)
        state.preview.scroll = 3  # second chunk of source line 2 in wrapped mode

        def rebuild_screen_lines(*, columns=None, preserve_scroll: bool = True) -> None:
            del columns
            state.preview.lines = build_screen_lines(state.preview.rendered, width, wrap=state.preview.wrap)
            state.preview.max_scroll = max(0, len(state.preview.lines) - visible_rows)
            if preserve_scroll:
                state.preview.scroll = max(0, min(state.preview.scroll, state.preview.max_scroll))
            else:
                state.preview.scroll = 0
            if state.preview.wrap:
                state.preview.horizontal_scroll = 0

        ops = NavigationController(
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
            open_tree_filter=lambda _mode: None,
        )

        ops.toggle_wrap_mode()

        self.assertFalse(state.preview.wrap)
        self.assertEqual(state.preview.scroll, 1)  # source line 2 in unwrapped mode

    def test_set_named_mark_persists_marks(self) -> None:
        visible_rows = 8
        width = 80
        rendered = "first\nsecond\n"
        state = _make_state(wrap_text=False, rendered=rendered, visible_rows=visible_rows, width=width)
        state.preview.scroll = 5
        state.preview.horizontal_scroll = 2

        ops = NavigationController(
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
            open_tree_filter=lambda _mode: None,
        )

        with mock.patch("lazyviewer.tree_pane.panels.picker.controller.save_named_marks") as save_named_marks:
            self.assertTrue(ops.set_named_mark("a"))

        self.assertIn("a", state.navigation.marks)
        self.assertEqual(state.navigation.marks["a"].start, 5)
        self.assertEqual(state.navigation.marks["a"].text_x, 2)
        save_named_marks.assert_called_once_with(state.navigation.marks)

    def test_add_workspace_root_from_selected_target_tracks_root_list(self) -> None:
        visible_rows = 8
        width = 80
        root = Path("/tmp").resolve()
        nested = root / "nested"
        state = _make_state(wrap_text=False, rendered="first\n", visible_rows=visible_rows, width=width)
        state.workspace.entries = [
            TreeEntry(path=root, depth=0, is_dir=True),
            TreeEntry(path=nested, depth=1, is_dir=True),
        ]
        state.workspace.selected = 1
        rebuild_calls: list[tuple[Path, Path | None]] = []

        ops = NavigationController(
            state=state,
            command_palette_items=(),
            rebuild_screen_lines=lambda **_kwargs: None,
            rebuild_tree_entries=lambda **kwargs: rebuild_calls.append(
                (
                    kwargs["preferred_path"].resolve(),
                    kwargs.get("preferred_workspace_root").resolve() if kwargs.get("preferred_workspace_root") else None,
                )
            ),
            preview_selected_entry=lambda **_kwargs: None,
            schedule_tree_filter_index_warmup=lambda: None,
            mark_tree_watch_dirty=lambda: None,
            reset_git_watch_context=lambda: None,
            refresh_git_status_overlay=lambda **_kwargs: None,
            visible_content_rows=lambda: visible_rows,
            refresh_rendered_for_current_path=lambda **_kwargs: None,
            open_tree_filter=lambda _mode: None,
        )

        ops.add_workspace_root_from_selected_target()

        self.assertEqual(state.workspace.active_root, root)
        self.assertEqual(state.workspace.roots, [root, nested])
        self.assertEqual(rebuild_calls[-1], (nested, nested))

    def test_remove_active_workspace_root_keeps_last_root_and_sets_status(self) -> None:
        visible_rows = 8
        width = 80
        state = _make_state(wrap_text=False, rendered="first\n", visible_rows=visible_rows, width=width)
        root = state.workspace.active_root.resolve()
        nested = root / "nested"
        state.workspace.roots = [root, nested]
        state.workspace.active_root = root
        state.workspace.current_path = nested / "demo.py"
        state.workspace.entries = [
            TreeEntry(path=root, depth=0, is_dir=True),
            TreeEntry(path=nested, depth=0, is_dir=True),
        ]
        state.workspace.selected = 1
        rebuild_calls: list[Path] = []

        ops = NavigationController(
            state=state,
            command_palette_items=(),
            rebuild_screen_lines=lambda **_kwargs: None,
            rebuild_tree_entries=lambda **kwargs: rebuild_calls.append(kwargs["preferred_path"].resolve()),
            preview_selected_entry=lambda **_kwargs: None,
            schedule_tree_filter_index_warmup=lambda: None,
            mark_tree_watch_dirty=lambda: None,
            reset_git_watch_context=lambda: None,
            refresh_git_status_overlay=lambda **_kwargs: None,
            visible_content_rows=lambda: visible_rows,
            refresh_rendered_for_current_path=lambda **_kwargs: None,
            open_tree_filter=lambda _mode: None,
        )

        ops.remove_active_workspace_root()
        self.assertEqual(state.workspace.active_root, root)
        self.assertEqual(state.workspace.roots, [root])
        self.assertEqual(rebuild_calls[-1], (nested / "demo.py").resolve())

        state.interface.status_message = ""
        ops.remove_active_workspace_root()
        self.assertEqual(state.workspace.active_root, root)
        self.assertEqual(state.workspace.roots, [root])
        self.assertIn("cannot delete", state.interface.status_message)

    def test_reroot_to_parent_replaces_selected_workspace_root_in_place(self) -> None:
        visible_rows = 8
        width = 80
        state = _make_state(wrap_text=False, rendered="first\n", visible_rows=visible_rows, width=width)
        root = state.workspace.active_root.resolve()
        nested = root / "nested"
        state.workspace.roots = [root, nested]
        state.workspace.active_root = root
        state.workspace.expanded_by_root = [{nested}, {nested}]
        state.workspace.expanded = {nested}
        state.workspace.entries = [
            TreeEntry(path=root, depth=0, is_dir=True, workspace_root=root),
            TreeEntry(path=nested, depth=1, is_dir=True, workspace_root=root),
            TreeEntry(path=nested, depth=0, is_dir=True, workspace_root=nested),
        ]
        state.workspace.selected = 2
        rebuild_calls: list[tuple[Path, Path | None]] = []

        ops = NavigationController(
            state=state,
            command_palette_items=(),
            rebuild_screen_lines=lambda **_kwargs: None,
            rebuild_tree_entries=lambda **kwargs: rebuild_calls.append(
                (
                    kwargs["preferred_path"].resolve(),
                    kwargs.get("preferred_workspace_root").resolve() if kwargs.get("preferred_workspace_root") else None,
                )
            ),
            preview_selected_entry=lambda **_kwargs: None,
            schedule_tree_filter_index_warmup=lambda: None,
            mark_tree_watch_dirty=lambda: None,
            reset_git_watch_context=lambda: None,
            refresh_git_status_overlay=lambda **_kwargs: None,
            visible_content_rows=lambda: visible_rows,
            refresh_rendered_for_current_path=lambda **_kwargs: None,
            open_tree_filter=lambda _mode: None,
        )

        ops.reroot_to_parent()

        self.assertEqual(state.workspace.roots, [root, root])
        self.assertEqual(state.workspace.active_root, root)
        self.assertEqual(rebuild_calls[-1], (nested, root))


if __name__ == "__main__":
    unittest.main()
