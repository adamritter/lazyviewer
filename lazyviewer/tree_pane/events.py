"""Tree-pane click interpretation for selection, toggling, and activation.

The handlers in this module translate left-pane pointer coordinates into tree
entry intents while staying thin on side effects. They centralize query-row
focus rules, directory-arrow toggles, and double-click activation semantics so
runtime wiring can inject preview/rebuild/copy behavior consistently.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time

from ..runtime.state import AppState


@dataclass(frozen=True)
class TreePaneMouseCallbacks:
    """Dependencies required by :class:`TreePaneMouseHandlers`.

    The handlers are intentionally side-effect thin: they compute click intent and
    delegate tree rebuild, preview, and clipboard behavior to injected callbacks so
    runtime wiring and tests can control those effects.
    """

    visible_content_rows: Callable[[], int]
    rebuild_tree_entries: Callable[..., None]
    mark_tree_watch_dirty: Callable[[], None]
    coerce_tree_filter_result_index: Callable[[int], int | None]
    preview_selected_entry: Callable[..., None]
    activate_tree_filter_selection: Callable[[], None]
    copy_text_to_clipboard: Callable[[str], bool]
    monotonic: Callable[[], float] = time.monotonic


class TreePaneMouseHandlers:
    """Interpret left-pane mouse clicks and mutate tree-selection state.

    This class owns click-to-row mapping (including the optional filter query row),
    single-vs-double click detection, and directory toggling rules for both normal
    tree mode and content-search mode.
    """

    def __init__(
        self,
        state: AppState,
        callbacks: TreePaneMouseCallbacks,
        double_click_seconds: float,
    ) -> None:
        """Create click handlers bound to shared app state.

        Args:
            state: Mutable runtime state to update in place.
            callbacks: Side-effect callbacks used to rebuild/preview/copy.
            double_click_seconds: Max interval between clicks to treat as a
                double-click on the same row.
        """
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
        """Handle a tree-pane pointer click and always consume the event.

        Behavior summary:
        - Clicks outside the visible tree pane are ignored (but consumed).
        - Clicking the filter query row enters query editing mode.
        - Clicking a directory arrow toggles expand/collapse immediately on
          press, then clears double-click history.
        - Other clicks select + preview the target row. A second click within
          ``double_click_seconds`` activates the selection: directories toggle,
          files copy their basename, and active filter sessions delegate to
          filter-activation behavior.
        """
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
        """Toggle a directory and rebuild the rendered tree snapshot.

        In content-search mode with ``content_mode_toggle=True``, collapsed state
        is tracked in ``state.tree_filter_collapsed_dirs`` so subtree visibility is
        local to that search session. In all other cases this flips membership in
        ``state.expanded``.
        """
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
