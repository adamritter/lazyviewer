"""Picker open/close/activate lifecycle operations."""

from __future__ import annotations

from pathlib import Path

from ..source_pane.symbols import collect_symbols


class PickerLifecycleMixin:
    """Symbol picker and command palette lifecycle methods."""

    def resolve_symbol_target(self) -> Path | None:
        """Resolve file path whose symbols should populate the symbol picker."""
        if self.state.current_path.is_file():
            return self.state.current_path.resolve()
        if not self.state.tree_entries:
            return None
        entry = self.state.tree_entries[self.state.selected_idx]
        if entry.is_dir or not entry.path.is_file():
            return None
        return entry.path.resolve()

    def open_symbol_picker(self) -> None:
        """Enter symbol-picker mode and populate symbols for current file target."""
        if not self.state.picker_active:
            self.state.picker_prev_browser_visible = self.state.browser_visible
        self.state.picker_active = True
        self.state.picker_mode = "symbols"
        self.state.picker_focus = "query"
        self.state.picker_message = ""
        self.state.picker_query = ""
        self.state.picker_selected = 0
        self.state.picker_list_start = 0
        self.state.picker_matches = []
        self.state.picker_match_labels = []
        self.state.picker_match_lines = []
        self.state.picker_match_commands = []
        self.state.picker_command_ids = []
        self.state.picker_command_labels = []
        was_browser_visible = self.state.browser_visible
        self.state.browser_visible = True
        if self.state.wrap_text and not was_browser_visible:
            self.rebuild_screen_lines()

        target = self.resolve_symbol_target()
        self.state.picker_symbol_file = target
        self.state.picker_symbol_labels = []
        self.state.picker_symbol_lines = []
        if target is None:
            self.state.picker_message = " no file selected"
            self.state.dirty = True
            return

        symbols, error = collect_symbols(target)
        if error:
            self.state.picker_message = f" {error}"
            self.state.dirty = True
            return

        self.state.picker_symbol_labels = [symbol.label for symbol in symbols]
        self.state.picker_symbol_lines = [symbol.line for symbol in symbols]
        if not self.state.picker_symbol_labels:
            self.state.picker_message = " no functions/classes/imports found"
            self.state.dirty = True
            return

        self.refresh_symbol_picker_matches(reset_selection=True)
        self.state.dirty = True

    def open_command_picker(self) -> None:
        """Enter command-palette mode and load command label/id lists."""
        if not self.state.picker_active:
            self.state.picker_prev_browser_visible = self.state.browser_visible
        self.state.picker_active = True
        self.state.picker_mode = "commands"
        self.state.picker_focus = "tree"
        self.state.picker_message = ""
        self.state.picker_query = ""
        self.state.picker_selected = 0
        self.state.picker_list_start = 0
        self.state.picker_matches = []
        self.state.picker_match_labels = []
        self.state.picker_match_lines = []
        self.state.picker_match_commands = []
        self.state.picker_symbol_file = None
        self.state.picker_symbol_labels = []
        self.state.picker_symbol_lines = []
        self.state.picker_command_ids = [command_id for command_id, _ in self.command_palette_items]
        self.state.picker_command_labels = [label for _, label in self.command_palette_items]
        was_browser_visible = self.state.browser_visible
        self.state.browser_visible = True
        if self.state.wrap_text and not was_browser_visible:
            self.rebuild_screen_lines()

        self.refresh_command_picker_matches(reset_selection=True)
        self.state.dirty = True

    def close_picker(self, reset_query: bool = True) -> None:
        """Close picker UI and restore non-picker browser visibility state."""
        previous_browser_visible = self.state.picker_prev_browser_visible
        self.state.picker_active = False
        if reset_query:
            self.state.picker_query = ""
        self.state.picker_mode = "symbols"
        self.state.picker_focus = "query"
        self.state.picker_message = ""
        self.state.picker_selected = 0
        self.state.picker_list_start = 0
        self.state.picker_matches = []
        self.state.picker_match_labels = []
        self.state.picker_match_lines = []
        self.state.picker_match_commands = []
        self.state.picker_symbol_file = None
        self.state.picker_symbol_labels = []
        self.state.picker_symbol_lines = []
        self.state.picker_command_ids = []
        self.state.picker_command_labels = []
        self.state.picker_prev_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = self.state.browser_visible != previous_browser_visible
            self.state.browser_visible = previous_browser_visible
            if self.state.wrap_text and browser_visibility_changed:
                self.rebuild_screen_lines()
        self.state.dirty = True

    def activate_picker_selection(self) -> bool:
        """Activate current picker row for symbols or command palette actions."""
        if self.state.picker_mode == "symbols" and self.state.picker_match_lines:
            selected_line = self.state.picker_match_lines[self.state.picker_selected]
            symbol_file = self.state.picker_symbol_file
            origin = self.current_jump_location()
            self.close_picker()
            if symbol_file is not None and symbol_file.resolve() != self.state.current_path.resolve():
                self.jump_to_path(symbol_file.resolve())
            self.jump_to_line(selected_line)
            self.record_jump_if_changed(origin)
            return False
        if self.state.picker_mode == "commands" and self.state.picker_match_commands:
            command_id = self.state.picker_match_commands[self.state.picker_selected]
            self.close_picker()
            return self.execute_command_palette_action(command_id)
        return False
