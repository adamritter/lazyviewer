"""Picker and navigation controller with explicit methods (no mixins)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from ....runtime.config import save_show_hidden
from ....runtime.state import AppState
from ....search.fuzzy import fuzzy_match_labels
from . import navigation as picker_navigation
from .line_map import first_display_index_for_source_line, source_line_for_display_index
from .panel import PickerPanel

PICKER_RESULT_LIMIT = 200


class NavigationController:
    """State-bound picker/navigation operations used by key handlers."""

    def __init__(
        self,
        *,
        state: AppState,
        command_palette_items: tuple[tuple[str, str], ...],
        rebuild_screen_lines: Callable[..., None],
        rebuild_tree_entries: Callable[..., None],
        preview_selected_entry: Callable[..., None],
        schedule_tree_filter_index_warmup: Callable[[], None],
        mark_tree_watch_dirty: Callable[[], None],
        reset_git_watch_context: Callable[[], None],
        refresh_git_status_overlay: Callable[..., None],
        visible_content_rows: Callable[[], int],
        refresh_rendered_for_current_path: Callable[..., None],
        open_tree_filter: Callable[[str], None],
    ) -> None:
        """Bind controller methods from explicit runtime hooks."""

        self.state = state
        self.command_palette_items = command_palette_items
        self.rebuild_screen_lines = rebuild_screen_lines
        self.rebuild_tree_entries = rebuild_tree_entries
        self.preview_selected_entry = preview_selected_entry
        self.schedule_tree_filter_index_warmup = schedule_tree_filter_index_warmup
        self.mark_tree_watch_dirty = mark_tree_watch_dirty
        self.reset_git_watch_context = reset_git_watch_context
        self.refresh_git_status_overlay = refresh_git_status_overlay
        self.visible_content_rows = visible_content_rows
        self.refresh_rendered_for_current_path = refresh_rendered_for_current_path
        self.open_tree_filter = open_tree_filter
        self.panel = PickerPanel(self)

    # matching
    def refresh_symbol_picker_matches(self, reset_selection: bool = False) -> None:
        """Recompute visible symbol matches from current picker query."""
        matched = fuzzy_match_labels(
            self.state.picker_query,
            self.state.picker_symbol_labels,
            limit=PICKER_RESULT_LIMIT,
        )
        self.state.picker_matches = []
        self.state.picker_match_labels = [label for _, label, _ in matched]
        self.state.picker_match_lines = [self.state.picker_symbol_lines[idx] for idx, _, _ in matched]
        self.state.picker_match_commands = []
        if self.state.picker_match_labels:
            self.state.picker_message = ""
        elif not self.state.picker_message:
            self.state.picker_message = " no matching symbols"
        self.state.picker_selected = 0 if reset_selection else max(
            0,
            min(self.state.picker_selected, max(0, len(self.state.picker_match_labels) - 1)),
        )
        if reset_selection or not self.state.picker_match_labels:
            self.state.picker_list_start = 0

    def refresh_command_picker_matches(self, reset_selection: bool = False) -> None:
        """Recompute command palette matches from current picker query."""
        matched = fuzzy_match_labels(
            self.state.picker_query,
            self.state.picker_command_labels,
            limit=PICKER_RESULT_LIMIT,
        )
        self.state.picker_matches = []
        self.state.picker_match_labels = [label for _, label, _ in matched]
        self.state.picker_match_lines = []
        self.state.picker_match_commands = [self.state.picker_command_ids[idx] for idx, _, _ in matched]
        if self.state.picker_match_labels:
            self.state.picker_message = ""
        elif not self.state.picker_message:
            self.state.picker_message = " no matching commands"
        self.state.picker_selected = 0 if reset_selection else max(
            0,
            min(self.state.picker_selected, max(0, len(self.state.picker_match_labels) - 1)),
        )
        if reset_selection or not self.state.picker_match_labels:
            self.state.picker_list_start = 0

    def refresh_active_picker_matches(self, reset_selection: bool = False) -> None:
        """Refresh matches for whichever picker mode is currently active."""
        if self.state.picker_mode == "commands":
            self.refresh_command_picker_matches(reset_selection=reset_selection)
            return
        self.refresh_symbol_picker_matches(reset_selection=reset_selection)

    # picker key handling
    def handle_picker_key(self, key: str, double_click_seconds: float) -> tuple[bool, bool]:
        """Handle one key while picker is active."""
        return self.panel.handle_key(key, double_click_seconds)

    # history/navigation
    def current_jump_location(self) -> picker_navigation.JumpLocation:
        """Capture current file path and scroll offsets as a jump location."""
        return picker_navigation.JumpLocation(
            path=self.state.current_path.resolve(),
            start=max(0, self.state.start),
            text_x=max(0, self.state.text_x),
        )

    def record_jump_if_changed(self, origin: picker_navigation.JumpLocation) -> None:
        """Record ``origin`` in jump history only when position actually changed."""
        normalized_origin = origin.normalized()
        if self.current_jump_location() == normalized_origin:
            return
        self.state.jump_history.record(normalized_origin)

    def apply_jump_location(self, location: picker_navigation.JumpLocation) -> bool:
        """Apply jump location and clamp offsets to current rendered content."""
        target = location.normalized()
        current_path = self.state.current_path.resolve()
        path_changed = target.path != current_path
        if path_changed:
            self.jump_to_path(target.path)

        self.state.max_start = max(0, len(self.state.lines) - self.visible_content_rows())
        clamped_start = max(0, min(target.start, self.state.max_start))
        clamped_text_x = 0 if self.state.wrap_text else max(0, target.text_x)
        prev_start = self.state.start
        prev_text_x = self.state.text_x
        self.state.start = clamped_start
        self.state.text_x = clamped_text_x
        return path_changed or self.state.start != prev_start or self.state.text_x != prev_text_x

    def jump_back_in_history(self) -> bool:
        """Jump to previous history location, returning whether state changed."""
        target = self.state.jump_history.go_back(self.current_jump_location())
        if target is None:
            return False
        return self.apply_jump_location(target)

    def jump_forward_in_history(self) -> bool:
        """Jump to next history location, returning whether state changed."""
        target = self.state.jump_history.go_forward(self.current_jump_location())
        if target is None:
            return False
        return self.apply_jump_location(target)

    def set_named_mark(self, mark_key: str) -> bool:
        """Store current jump location under a valid named-mark key."""
        if not picker_navigation.is_named_mark_key(mark_key):
            return False
        self.state.named_marks[mark_key] = self.current_jump_location()
        picker_navigation.save_named_marks(self.state.named_marks)
        return True

    def jump_to_named_mark(self, mark_key: str) -> bool:
        """Jump to saved named mark and push current location onto history."""
        if not picker_navigation.is_named_mark_key(mark_key):
            return False
        target = self.state.named_marks.get(mark_key)
        if target is None:
            return False
        origin = self.current_jump_location()
        if target.normalized() == origin:
            return False
        self.state.jump_history.record(origin)
        return self.apply_jump_location(target)

    def reveal_path_in_tree(self, target: Path) -> None:
        """Expand ancestor directories and rebuild tree focused on ``target``."""
        target = target.resolve()
        if target != self.state.tree_root:
            parent = target.parent
            while True:
                resolved = parent.resolve()
                if resolved == self.state.tree_root:
                    break
                self.state.expanded.add(resolved)
                if resolved.parent == resolved:
                    break
                parent = resolved.parent
        self.state.expanded.add(self.state.tree_root)
        self.rebuild_tree_entries(preferred_path=target, center_selection=True)
        self.mark_tree_watch_dirty()

    def jump_to_path(self, target: Path) -> None:
        """Reveal and open ``target`` path in tree and source preview state."""
        target = target.resolve()
        self.reveal_path_in_tree(target)
        self.state.current_path = target
        self.refresh_rendered_for_current_path()

    def jump_to_line(self, line_number: int) -> None:
        """Scroll source preview near ``line_number`` and reset horizontal offset."""
        visible_rows = max(1, self.visible_content_rows())
        self.state.max_start = max(0, len(self.state.lines) - visible_rows)
        max_line_index = max(0, len(self.state.lines) - 1)
        anchor = max(0, min(line_number, max_line_index))
        centered = max(0, anchor - max(1, visible_rows // 3))
        self.state.start = max(0, min(centered, self.state.max_start))
        self.state.text_x = 0

    # view actions
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
            self.open_tree_filter("files")
            return False
        if command_id == "search_content":
            self.open_tree_filter("content")
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

    # picker lifecycle
    def resolve_symbol_target(self) -> Path | None:
        """Resolve file path whose symbols should populate the symbol picker."""
        return self.panel.resolve_symbol_target()

    def open_symbol_picker(self) -> None:
        """Enter symbol-picker mode and populate symbols for current file target."""
        self.panel.open_symbol_picker()

    def open_command_picker(self) -> None:
        """Enter command-palette mode and load command label/id lists."""
        self.panel.open_command_picker()

    def close_picker(self, reset_query: bool = True) -> None:
        """Close picker UI and restore non-picker browser visibility state."""
        self.panel.close_picker(reset_query=reset_query)

    def activate_picker_selection(self) -> bool:
        """Activate current picker row for symbols or command palette actions."""
        return self.panel.activate_picker_selection()
