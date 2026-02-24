"""Tree-filter prompt lifecycle and activation behavior."""

from __future__ import annotations

from ...runtime.navigation import JumpLocation


class TreeFilterLifecycleMixin:
    """Mode prompt text, open/close, and activation handlers."""

    def get_loading_until(self) -> float:
        """Return timestamp until which loading indicator should remain visible."""
        return self.loading_until

    def tree_filter_prompt_prefix(self) -> str:
        """Return prompt prefix for active filter mode."""
        return "/>" if self.state.tree_filter_mode == "content" else "p>"

    def tree_filter_placeholder(self) -> str:
        """Return placeholder text for active filter mode."""
        return "type to search content" if self.state.tree_filter_mode == "content" else "type to filter files"

    def tree_view_rows(self) -> int:
        """Return visible tree rows, reserving one row for active filter prompt."""
        rows = self.visible_content_rows()
        if self.state.tree_filter_active and not self.state.picker_active:
            return max(1, rows - 1)
        return rows

    def reset_tree_filter_session_state(self) -> None:
        """Reset per-session transient filter state."""
        self.state.tree_filter_loading = False
        self.state.tree_filter_collapsed_dirs = set()

    def open_tree_filter(self, mode: str = "files") -> None:
        """Open filter panel in requested mode and initialize session fields."""
        was_active = self.state.tree_filter_active
        previous_mode = self.state.tree_filter_mode
        if not self.state.tree_filter_active:
            self.state.tree_filter_prev_browser_visible = self.state.browser_visible
        was_browser_visible = self.state.browser_visible
        self.state.browser_visible = True
        if self.state.wrap_text and not was_browser_visible:
            self.rebuild_screen_lines()
        self.state.tree_filter_active = True
        self.state.tree_filter_mode = mode
        self.state.tree_filter_editing = True
        self.state.tree_filter_origin = self.current_jump_location() if mode == "content" else None
        self.state.tree_filter_query = ""
        self.state.tree_filter_match_count = 0
        self.state.tree_filter_truncated = False
        self.reset_tree_filter_session_state()
        if was_active and previous_mode != mode:
            self.rebuild_tree_entries(preferred_path=self.state.current_path.resolve())
        self.state.dirty = True
        if self.on_tree_filter_state_change is not None:
            self.on_tree_filter_state_change()

    def close_tree_filter(self, clear_query: bool = True, restore_origin: bool = False) -> None:
        """Close filter panel, optionally restoring original content-search position."""
        previous_browser_visible = self.state.tree_filter_prev_browser_visible
        restore_location: JumpLocation | None = None
        if restore_origin and self.state.tree_filter_mode == "content" and self.state.tree_filter_origin is not None:
            restore_location = self.state.tree_filter_origin.normalized()
        self.state.tree_filter_active = False
        self.state.tree_filter_editing = False
        self.state.tree_filter_mode = "files"
        if clear_query:
            self.state.tree_filter_query = ""
            self.state.tree_filter_truncated = False
        self.reset_tree_filter_session_state()
        self.state.tree_filter_prev_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = self.state.browser_visible != previous_browser_visible
            self.state.browser_visible = previous_browser_visible
            if self.state.wrap_text and browser_visibility_changed:
                self.rebuild_screen_lines()
        if restore_location is not None:
            self.jump_to_path(restore_location.path)
            self.state.max_start = max(0, len(self.state.lines) - self.visible_content_rows())
            self.state.start = max(0, min(restore_location.start, self.state.max_start))
            self.state.text_x = 0 if self.state.wrap_text else max(0, restore_location.text_x)
        else:
            self.rebuild_tree_entries(preferred_path=self.state.current_path.resolve())
        self.state.tree_filter_origin = None
        self.state.dirty = True
        if self.on_tree_filter_state_change is not None:
            self.on_tree_filter_state_change()

    def activate_tree_filter_selection(self) -> None:
        """Activate selected filter result according to current filter mode."""
        if not self.state.tree_entries:
            if self.state.tree_filter_mode == "content":
                self.state.tree_filter_editing = False
                self.state.dirty = True
            else:
                self.close_tree_filter(clear_query=True)
            return

        entry = self.state.tree_entries[self.state.selected_idx]
        if entry.is_dir:
            candidate_idx = self.nearest_tree_filter_result_entry_index(self.state.selected_idx)
            if candidate_idx is None:
                self.close_tree_filter(clear_query=True)
                return
            self.state.selected_idx = candidate_idx
            entry = self.state.tree_entries[self.state.selected_idx]

        selected_path = entry.path.resolve()
        selected_line = entry.line if entry.kind == "search_hit" else None
        if self.state.tree_filter_mode == "content":
            # Keep content-search mode active after Enter/double-click; Esc exits.
            origin = self.current_jump_location()
            self.state.tree_filter_editing = False
            self.preview_selected_entry()
            self.record_jump_if_changed(origin)
            self.state.dirty = True
            return

        origin = self.current_jump_location()
        self.close_tree_filter(clear_query=True)
        self.jump_to_path(selected_path)
        if selected_line is not None:
            self.jump_to_line(max(0, selected_line - 1))
        self.record_jump_if_changed(origin)
        self.state.dirty = True
