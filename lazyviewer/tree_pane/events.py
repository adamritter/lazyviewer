"""Tree-pane mouse event helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time

from ..state import AppState


@dataclass(frozen=True)
class TreePaneMouseCallbacks:
    visible_content_rows: Callable[[], int]
    rebuild_tree_entries: Callable[..., None]
    mark_tree_watch_dirty: Callable[[], None]
    coerce_tree_filter_result_index: Callable[[int], int | None]
    preview_selected_entry: Callable[..., None]
    activate_tree_filter_selection: Callable[[], None]
    copy_text_to_clipboard: Callable[[str], bool]
    monotonic: Callable[[], float] = time.monotonic


class TreePaneMouseHandlers:
    def __init__(
        self,
        state: AppState,
        callbacks: TreePaneMouseCallbacks,
        double_click_seconds: float,
    ) -> None:
        self._state = state
        self._visible_content_rows = callbacks.visible_content_rows
        self._rebuild_tree_entries = callbacks.rebuild_tree_entries
        self._mark_tree_watch_dirty = callbacks.mark_tree_watch_dirty
        self._coerce_tree_filter_result_index = callbacks.coerce_tree_filter_result_index
        self._preview_selected_entry = callbacks.preview_selected_entry
        self._activate_tree_filter_selection = callbacks.activate_tree_filter_selection
        self._copy_text_to_clipboard = callbacks.copy_text_to_clipboard
        self._monotonic = callbacks.monotonic
        self._double_click_seconds = double_click_seconds

    def handle_click(self, col: int, row: int, is_left_down: bool) -> bool:
        state = self._state
        if not (state.browser_visible and 1 <= row <= self._visible_content_rows() and col <= state.left_width):
            return True

        query_row_visible = state.tree_filter_active
        if query_row_visible and row == 1:
            state.tree_filter_editing = True
            state.dirty = True
            return True

        raw_clicked_idx = state.tree_start + (row - 1 - (1 if query_row_visible else 0))
        if not (0 <= raw_clicked_idx < len(state.tree_entries)):
            return True

        raw_clicked_entry = state.tree_entries[raw_clicked_idx]
        raw_arrow_col = 1 + (raw_clicked_entry.depth * 2)
        if is_left_down and raw_clicked_entry.is_dir and raw_arrow_col <= col <= (raw_arrow_col + 1):
            resolved = raw_clicked_entry.path.resolve()
            self._toggle_directory_entry(resolved, content_mode_toggle=True)
            state.last_click_idx = -1
            state.last_click_time = 0.0
            return True

        clicked_idx = self._coerce_tree_filter_result_index(raw_clicked_idx)
        if clicked_idx is None:
            return True

        prev_selected = state.selected_idx
        state.selected_idx = clicked_idx
        self._preview_selected_entry()
        if state.selected_idx != prev_selected:
            state.dirty = True

        now = self._monotonic()
        is_double = clicked_idx == state.last_click_idx and (now - state.last_click_time) <= self._double_click_seconds
        state.last_click_idx = clicked_idx
        state.last_click_time = now
        if not is_double:
            return True

        if state.tree_filter_active and state.tree_filter_query:
            self._activate_tree_filter_selection()
            return True

        entry = state.tree_entries[state.selected_idx]
        if entry.is_dir:
            resolved = entry.path.resolve()
            self._toggle_directory_entry(resolved)
            return True

        self._copy_text_to_clipboard(entry.path.name)
        state.dirty = True
        return True

    def _toggle_directory_entry(
        self,
        resolved: Path,
        content_mode_toggle: bool = False,
    ) -> None:
        state = self._state
        if content_mode_toggle and state.tree_filter_active and state.tree_filter_mode == "content":
            if resolved in state.tree_filter_collapsed_dirs:
                state.tree_filter_collapsed_dirs.remove(resolved)
                state.expanded.add(resolved)
            else:
                if resolved != state.tree_root:
                    state.tree_filter_collapsed_dirs.add(resolved)
                state.expanded.discard(resolved)
        else:
            state.expanded.symmetric_difference_update({resolved})
        self._rebuild_tree_entries(preferred_path=resolved)
        self._mark_tree_watch_dirty()
        state.dirty = True
