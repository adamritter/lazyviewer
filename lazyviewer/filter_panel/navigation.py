"""Selection and result-navigation helpers for tree-filter mode."""

from __future__ import annotations

from ..tree_pane.model import next_file_entry_index


class TreeFilterNavigationMixin:
    """Selection movement and hit-jump behavior."""

    def next_content_hit_entry_index(self, selected_idx: int, direction: int) -> int | None:
        """Return next search-hit entry index from selected index."""
        if not self.state.tree_entries or direction == 0:
            return None
        step = 1 if direction > 0 else -1
        idx = selected_idx + step
        while 0 <= idx < len(self.state.tree_entries):
            if self.state.tree_entries[idx].kind == "search_hit":
                return idx
            idx += step
        return None

    def next_tree_filter_result_entry_index(self, selected_idx: int, direction: int) -> int | None:
        """Return next result row index for active filter mode."""
        if self.state.tree_filter_mode == "content":
            return self.next_content_hit_entry_index(selected_idx, direction)
        return next_file_entry_index(self.state.tree_entries, selected_idx, direction)

    def nearest_tree_filter_result_entry_index(self, selected_idx: int) -> int | None:
        """Return closest result row index around selected index."""
        candidate_idx = self.next_tree_filter_result_entry_index(selected_idx, 1)
        if candidate_idx is None:
            candidate_idx = self.next_tree_filter_result_entry_index(selected_idx, -1)
        return candidate_idx

    def coerce_tree_filter_result_index(self, idx: int) -> int | None:
        """Coerce arbitrary row index onto nearest selectable filter result."""
        if not (0 <= idx < len(self.state.tree_entries)):
            return None
        if not (self.state.tree_filter_active and self.state.tree_filter_query):
            return idx

        entry = self.state.tree_entries[idx]
        if self.state.tree_filter_mode == "content":
            if entry.kind == "search_hit":
                return idx
        elif not entry.is_dir:
            return idx

        return self.nearest_tree_filter_result_entry_index(idx)

    def move_tree_selection(self, direction: int) -> bool:
        """Move tree selection, honoring filter-result-only navigation when active."""
        if not self.state.tree_entries or direction == 0:
            return False

        if self.state.tree_filter_active and self.state.tree_filter_query:
            target_idx = self.next_tree_filter_result_entry_index(self.state.selected_idx, direction)
            if target_idx is None:
                return False
        else:
            step = 1 if direction > 0 else -1
            target_idx = max(0, min(len(self.state.tree_entries) - 1, self.state.selected_idx + step))

        if target_idx == self.state.selected_idx:
            return False

        self.state.selected_idx = target_idx
        self.preview_selected_entry()
        return True

    def jump_to_next_content_hit(self, direction: int) -> bool:
        """Jump to next/previous content hit with wrap-around behavior."""
        if direction == 0:
            return False
        if direction > 0:
            target_idx = self.next_content_hit_entry_index(self.state.selected_idx, 1)
            if target_idx is None:
                target_idx = self.next_content_hit_entry_index(-1, 1)
        else:
            target_idx = self.next_content_hit_entry_index(self.state.selected_idx, -1)
            if target_idx is None:
                target_idx = self.next_content_hit_entry_index(len(self.state.tree_entries), -1)

        if target_idx is None or target_idx == self.state.selected_idx:
            return False

        origin = self.current_jump_location()
        self.state.selected_idx = target_idx
        self.preview_selected_entry()
        self.record_jump_if_changed(origin)
        return True
