"""Tree-pane click interpretation for selection, toggling, and activation.

The handlers in this module translate left-pane pointer coordinates into tree
entry intents while staying thin on side effects. They centralize query-row
focus rules, directory-arrow toggles, and double-click activation semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import time

from ..runtime.state import AppState
from .workspace_roots import normalized_workspace_roots, workspace_root_banner_rows


class TreePaneMouseHandlers:
    """Interpret left-pane mouse clicks and mutate tree-selection state.

    This class owns click-to-row mapping (including the optional filter query row),
    single-vs-double click detection, and directory toggling rules for both normal
    tree mode and content-search mode.
    """

    def __init__(
        self,
        *,
        state: AppState,
        visible_content_rows: Callable[[], int],
        rebuild_tree_entries: Callable[..., None],
        mark_tree_watch_dirty: Callable[[], None],
        coerce_tree_filter_result_index: Callable[[int], int | None],
        preview_selected_entry: Callable[..., None],
        activate_tree_filter_selection: Callable[[], None],
        copy_text_to_clipboard: Callable[[str], bool],
        double_click_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create click handlers bound to shared app state.

        Args:
            state: Mutable runtime state to update in place.
            visible_content_rows: Visible tree row count provider.
            rebuild_tree_entries: Tree rebuild hook.
            mark_tree_watch_dirty: Tree-watch invalidation hook.
            coerce_tree_filter_result_index: Filter-result row resolver.
            preview_selected_entry: Selection preview hook.
            activate_tree_filter_selection: Active filter-row activation hook.
            copy_text_to_clipboard: Clipboard copy hook.
            double_click_seconds: Max interval between clicks to treat as a
                double-click on the same row.
            monotonic: Monotonic clock provider used for double-click timing.
        """
        self._state = state
        self._visible_content_rows = visible_content_rows
        self._rebuild_tree_entries = rebuild_tree_entries
        self._mark_tree_watch_dirty = mark_tree_watch_dirty
        self._coerce_tree_filter_result_index = coerce_tree_filter_result_index
        self._preview_selected_entry = preview_selected_entry
        self._activate_tree_filter_selection = activate_tree_filter_selection
        self._copy_text_to_clipboard = copy_text_to_clipboard
        self._monotonic = monotonic
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

        query_row_visible = (
            state.tree_filter_active
            and state.tree_filter_prompt_row_visible
            and not state.picker_active
        )
        if query_row_visible and row == 1:
            state.tree_filter_editing = True
            state.dirty = True
            return True

        root_row_count = workspace_root_banner_rows(
            state.tree_roots,
            state.tree_root,
            picker_active=state.picker_active,
        )
        row_offset = (1 if query_row_visible else 0) + root_row_count
        if row <= row_offset:
            return True

        raw_clicked_idx = state.tree_start + (row - 1 - row_offset)
        if not (0 <= raw_clicked_idx < len(state.tree_entries)):
            return True

        raw_clicked_entry = state.tree_entries[raw_clicked_idx]
        raw_workspace_root = (
            raw_clicked_entry.workspace_root.resolve()
            if raw_clicked_entry.workspace_root is not None
            else state.tree_root.resolve()
        )
        raw_arrow_col = 1 + (raw_clicked_entry.depth * 2)
        if is_left_down and raw_clicked_entry.is_dir and raw_arrow_col <= col <= (raw_arrow_col + 1):
            resolved = raw_clicked_entry.path.resolve()
            self._toggle_directory_entry(
                resolved,
                workspace_root=raw_workspace_root,
                content_mode_toggle=True,
            )
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
            workspace_root = (
                entry.workspace_root.resolve()
                if entry.workspace_root is not None
                else state.tree_root.resolve()
            )
            self._toggle_directory_entry(resolved, workspace_root=workspace_root)
            return True

        self._copy_text_to_clipboard(entry.path.name)
        state.dirty = True
        return True

    def _toggle_directory_entry(
        self,
        resolved: Path,
        workspace_root: Path,
        content_mode_toggle: bool = False,
    ) -> None:
        """Toggle a directory and rebuild the rendered tree snapshot.

        In content-search mode with ``content_mode_toggle=True``, collapsed state
        is tracked in ``state.tree_filter_collapsed_dirs`` so subtree visibility is
        local to that search session. In all other cases this flips membership in
        ``state.expanded``.
        """
        state = self._state
        roots = normalized_workspace_roots(state.tree_roots, state.tree_root)
        by_root: dict[Path, set[Path]] = {}
        for root in roots:
            existing = state.workspace_expanded.get(root)
            if existing is not None:
                normalized = {
                    candidate.resolve()
                    for candidate in existing
                    if candidate.resolve().is_relative_to(root)
                }
            else:
                normalized = {
                    candidate.resolve()
                    for candidate in state.expanded
                    if candidate.resolve().is_relative_to(root)
                }
            by_root[root] = normalized

        scope = workspace_root.resolve()
        scoped = set(by_root.get(scope, set()))

        if content_mode_toggle and state.tree_filter_active and state.tree_filter_mode == "content":
            if resolved in state.tree_filter_collapsed_dirs:
                state.tree_filter_collapsed_dirs.remove(resolved)
                scoped.add(resolved)
            else:
                state.tree_filter_collapsed_dirs.add(resolved)
                scoped.discard(resolved)
        else:
            if resolved in scoped:
                scoped.discard(resolved)
            else:
                scoped.add(resolved)
        by_root[scope] = scoped
        state.workspace_expanded = by_root
        state.expanded = set().union(*by_root.values())
        self._rebuild_tree_entries(preferred_path=resolved)
        self._mark_tree_watch_dirty()
        state.dirty = True
