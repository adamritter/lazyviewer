"""View toggles, rerooting, and command-palette actions."""

from __future__ import annotations

import shutil

from ....runtime.config import save_show_hidden
from .line_map import first_display_index_for_source_line, source_line_for_display_index


class ViewActionMixin:
    """UI actions used by command palette and keyboard shortcuts."""

    def current_term_columns(self) -> int:
        """Return current terminal width used for render rebuild calls."""
        return shutil.get_terminal_size((80, 24)).columns

    def reroot_to_parent(self) -> None:
        """Move tree root to parent directory, preserving previous root expanded."""
        old_root = self.state.tree_root.resolve()
        parent_root = old_root.parent.resolve()
        if parent_root == old_root:
            return
        self.state.tree_root = parent_root
        self.state.expanded = {self.state.tree_root, old_root}
        self.rebuild_tree_entries(preferred_path=old_root, center_selection=True)
        self.preview_selected_entry(force=True)
        self.schedule_tree_filter_index_warmup()
        self.mark_tree_watch_dirty()
        self.reset_git_watch_context()
        self.refresh_git_status_overlay(force=True)
        self.state.dirty = True

    def reroot_to_selected_target(self) -> None:
        """Reroot tree at selected entry (or current path fallback) directory."""
        selected_entry = (
            self.state.tree_entries[self.state.selected_idx]
            if self.state.tree_entries and 0 <= self.state.selected_idx < len(self.state.tree_entries)
            else None
        )
        if selected_entry is not None:
            selected_target = selected_entry.path.resolve()
            target_root = selected_target if selected_entry.is_dir else selected_target.parent.resolve()
        else:
            selected_target = self.state.current_path.resolve()
            target_root = selected_target if selected_target.is_dir() else selected_target.parent.resolve()

        old_root = self.state.tree_root.resolve()
        if target_root == old_root:
            return
        self.state.tree_root = target_root
        self.state.expanded = {self.state.tree_root}
        self.rebuild_tree_entries(preferred_path=selected_target, center_selection=True)
        self.preview_selected_entry(force=True)
        self.schedule_tree_filter_index_warmup()
        self.mark_tree_watch_dirty()
        self.reset_git_watch_context()
        self.refresh_git_status_overlay(force=True)
        self.state.dirty = True

    def toggle_hidden_files(self) -> None:
        """Toggle hidden-file visibility and persist preference to config."""
        self.state.show_hidden = not self.state.show_hidden
        save_show_hidden(self.state.show_hidden)
        selected_path = self.state.tree_entries[self.state.selected_idx].path.resolve() if self.state.tree_entries else self.state.tree_root
        self.rebuild_tree_entries(preferred_path=selected_path)
        self.preview_selected_entry(force=True)
        self.schedule_tree_filter_index_warmup()
        self.mark_tree_watch_dirty()
        self.state.dirty = True

    def toggle_tree_pane(self) -> None:
        """Toggle tree-pane visibility and reflow wrapped source when needed."""
        self.state.browser_visible = not self.state.browser_visible
        if self.state.wrap_text:
            self.rebuild_screen_lines(columns=self.current_term_columns())
        self.state.dirty = True

    def toggle_wrap_mode(self) -> None:
        """Toggle soft-wrap while preserving approximate top source-line context."""
        top_source_line = source_line_for_display_index(self.state.lines, self.state.start)
        self.state.wrap_text = not self.state.wrap_text
        if self.state.wrap_text:
            self.state.text_x = 0
        self.rebuild_screen_lines(columns=self.current_term_columns())
        self.state.start = first_display_index_for_source_line(self.state.lines, top_source_line)
        self.state.start = max(0, min(self.state.start, self.state.max_start))
        self.state.dirty = True

    def toggle_help_panel(self) -> None:
        """Toggle help panel visibility and keep selected search hit in view."""
        self.state.show_help = not self.state.show_help
        self.rebuild_screen_lines(columns=self.current_term_columns())
        self.ensure_selected_content_hit_visible()
        self.state.dirty = True

    def ensure_selected_content_hit_visible(self) -> None:
        """Adjust source scroll so selected content-hit anchor remains visible."""
        if not (
            self.state.tree_filter_active
            and self.state.tree_filter_mode == "content"
            and self.state.tree_filter_query
            and self.state.tree_entries
            and 0 <= self.state.selected_idx < len(self.state.tree_entries)
        ):
            return

        selected_entry = self.state.tree_entries[self.state.selected_idx]
        if selected_entry.kind != "search_hit" or selected_entry.line is None:
            return

        visible_rows = max(1, self.visible_content_rows())
        self.state.max_start = max(0, len(self.state.lines) - visible_rows)
        max_line_index = max(0, len(self.state.lines) - 1)
        anchor = max(0, min(selected_entry.line - 1, max_line_index))
        view_end = self.state.start + visible_rows - 1
        if self.state.start <= anchor <= view_end:
            return

        centered = max(0, anchor - max(1, visible_rows // 3))
        self.state.start = max(0, min(centered, self.state.max_start))

    def execute_command_palette_action(self, command_id: str) -> bool:
        """Execute command-palette action by id, returning ``True`` to quit."""
        if command_id == "filter_files":
            if self.open_tree_filter_fn is not None:
                self.open_tree_filter_fn("files")
            return False
        if command_id == "search_content":
            if self.open_tree_filter_fn is not None:
                self.open_tree_filter_fn("content")
            return False
        if command_id == "open_symbols":
            self.open_symbol_picker()
            return False
        if command_id == "history_back":
            if self.jump_back_in_history():
                self.state.dirty = True
            return False
        if command_id == "history_forward":
            if self.jump_forward_in_history():
                self.state.dirty = True
            return False
        if command_id == "toggle_tree":
            self.toggle_tree_pane()
            return False
        if command_id == "toggle_wrap":
            self.toggle_wrap_mode()
            return False
        if command_id == "toggle_hidden":
            self.toggle_hidden_files()
            return False
        if command_id == "toggle_help":
            self.toggle_help_panel()
            return False
        if command_id == "reroot_selected":
            self.reroot_to_selected_target()
            return False
        if command_id == "reroot_parent":
            self.reroot_to_parent()
            return False
        if command_id == "quit":
            return True
        return False
