"""Tree mouse-click row mapping for multi-root forest rendering."""

from __future__ import annotations

import unittest
from pathlib import Path

from lazyviewer.runtime.state import AppState
from lazyviewer.tree_model import TreeEntry
from lazyviewer.tree_pane.events import TreePaneMouseHandlers


def _make_state() -> AppState:
    root = Path("/tmp").resolve()
    nested = (root / "nested").resolve()
    child_file = nested / "demo.py"
    return AppState(
        current_path=child_file,
        tree_root=nested,
        tree_roots=[root, nested],
        expanded={nested},
        show_hidden=False,
        tree_entries=[
            TreeEntry(path=nested, depth=0, is_dir=True),
            TreeEntry(path=child_file, depth=1, is_dir=False),
        ],
        selected_idx=0,
        rendered="",
        lines=[""],
        start=0,
        tree_start=0,
        text_x=0,
        wrap_text=False,
        left_width=40,
        right_width=80,
        usable=20,
        max_start=0,
        last_right_width=80,
    )


class TreeMouseWorkspaceRootRowsTests(unittest.TestCase):
    def test_click_on_first_tree_row_selects_top_entry(self) -> None:
        state = _make_state()
        previews: list[int] = []
        handler = TreePaneMouseHandlers(
            state=state,
            visible_content_rows=lambda: 20,
            rebuild_tree_entries=lambda **_kwargs: None,
            mark_tree_watch_dirty=lambda: None,
            coerce_tree_filter_result_index=lambda idx: idx,
            preview_selected_entry=lambda **_kwargs: previews.append(state.selected_idx),
            activate_tree_filter_selection=lambda: None,
            copy_text_to_clipboard=lambda _text: True,
            double_click_seconds=0.3,
        )

        # Click directory name area (not arrow) on first visible row.
        handled = handler.handle_click(col=4, row=1, is_left_down=True)

        self.assertTrue(handled)
        self.assertEqual(state.selected_idx, 0)
        self.assertEqual(previews, [0])

    def test_click_on_second_tree_row_selects_second_entry(self) -> None:
        state = _make_state()
        previews: list[int] = []
        handler = TreePaneMouseHandlers(
            state=state,
            visible_content_rows=lambda: 20,
            rebuild_tree_entries=lambda **_kwargs: None,
            mark_tree_watch_dirty=lambda: None,
            coerce_tree_filter_result_index=lambda idx: idx,
            preview_selected_entry=lambda **_kwargs: previews.append(state.selected_idx),
            activate_tree_filter_selection=lambda: None,
            copy_text_to_clipboard=lambda _text: True,
            double_click_seconds=0.3,
        )

        handled = handler.handle_click(col=3, row=2, is_left_down=True)

        self.assertTrue(handled)
        self.assertEqual(state.selected_idx, 1)
        self.assertEqual(previews[-1], 1)
