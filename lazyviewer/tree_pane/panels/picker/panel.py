"""Picker panel UI element owning picker lifecycle and key handling."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from ....source_pane.symbols import collect_symbols

if TYPE_CHECKING:
    from .controller import NavigationController


class PickerPanel:
    """Stateful UI element for command/symbol picker interactions."""

    def __init__(self, owner: NavigationController) -> None:
        self.owner = owner

    @staticmethod
    def _parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
        parts = mouse_key.split(":")
        if len(parts) < 3:
            return None, None
        try:
            return int(parts[1]), int(parts[2])
        except Exception:
            return None, None

    def _move_selection(self, direction: int) -> None:
        if not self.owner.state.picker_match_labels:
            return
        previous = self.owner.state.picker_selected
        self.owner.state.picker_selected = max(
            0,
            min(len(self.owner.state.picker_match_labels) - 1, self.owner.state.picker_selected + direction),
        )
        if self.owner.state.picker_selected != previous:
            self.owner.state.dirty = True

    def _handle_mouse_wheel(self, mouse_key: str) -> None:
        direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
        col, _row = self._parse_mouse_col_row(mouse_key)
        if self.owner.state.browser_visible and col is not None and col <= self.owner.state.left_width:
            self._move_selection(direction)
            return
        previous_start = self.owner.state.start
        self.owner.state.start += direction * 3
        self.owner.state.start = max(0, min(self.owner.state.start, self.owner.state.max_start))
        if self.owner.state.start != previous_start:
            self.owner.state.dirty = True

    def _handle_mouse_click(
        self,
        mouse_key: str,
        visible_rows: int,
        double_click_seconds: float,
        *,
        focus_query_row: bool,
    ) -> bool:
        col, row = self._parse_mouse_col_row(mouse_key)
        if not (
            self.owner.state.browser_visible
            and col is not None
            and row is not None
            and 1 <= row <= visible_rows
            and col <= self.owner.state.left_width
        ):
            return False
        if row == 1:
            if focus_query_row:
                self.owner.state.picker_focus = "query"
                self.owner.state.dirty = True
            return False
        clicked_idx = self.owner.state.picker_list_start + (row - 2)
        if not (0 <= clicked_idx < len(self.owner.state.picker_match_labels)):
            return False
        previous = self.owner.state.picker_selected
        self.owner.state.picker_selected = clicked_idx
        if self.owner.state.picker_selected != previous:
            self.owner.state.dirty = True
        now = time.monotonic()
        is_double = clicked_idx == self.owner.state.last_click_idx and (
            now - self.owner.state.last_click_time
        ) <= double_click_seconds
        self.owner.state.last_click_idx = clicked_idx
        self.owner.state.last_click_time = now
        if not is_double:
            return False
        should_quit = self.owner.activate_picker_selection()
        if should_quit:
            return True
        self.owner.state.dirty = True
        return False

    def handle_key(self, key: str, double_click_seconds: float) -> tuple[bool, bool]:
        """Handle one key while picker is active."""
        key_lower = key.lower()

        if not self.owner.state.picker_active:
            return False, False

        if key == "ESC" or key == "\x03":
            self.owner.close_picker()
            return True, False

        if self.owner.state.picker_mode == "commands":
            if key == "UP" or key_lower == "k":
                self._move_selection(-1)
                return True, False
            if key == "DOWN" or key_lower == "j":
                self._move_selection(1)
                return True, False
            if key == "BACKSPACE":
                if self.owner.state.picker_query:
                    self.owner.state.picker_query = self.owner.state.picker_query[:-1]
                    self.owner.refresh_command_picker_matches(reset_selection=True)
                    self.owner.state.dirty = True
                return True, False
            if len(key) == 1 and key.isprintable():
                self.owner.state.picker_query += key
                self.owner.refresh_command_picker_matches(reset_selection=True)
                self.owner.state.dirty = True
                return True, False
            if key == "ENTER" or key_lower == "l":
                should_quit = self.owner.activate_picker_selection()
                if should_quit:
                    return True, True
                self.owner.state.dirty = True
                return True, False
            if key == "TAB":
                return True, False
            if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
                self._handle_mouse_wheel(key)
                return True, False
            if key.startswith("MOUSE_LEFT_DOWN:"):
                should_quit = self._handle_mouse_click(
                    key,
                    self.owner.visible_content_rows(),
                    double_click_seconds,
                    focus_query_row=False,
                )
                if should_quit:
                    return True, True
                return True, False
            return True, False

        if key == "TAB":
            self.owner.state.picker_focus = "tree" if self.owner.state.picker_focus == "query" else "query"
            self.owner.state.dirty = True
            return True, False

        if self.owner.state.picker_focus == "query":
            if key == "ENTER":
                self.owner.state.picker_focus = "tree"
                self.owner.state.dirty = True
                return True, False
            if key == "BACKSPACE":
                if self.owner.state.picker_query:
                    self.owner.state.picker_query = self.owner.state.picker_query[:-1]
                    self.owner.refresh_active_picker_matches(reset_selection=True)
                    self.owner.state.dirty = True
                return True, False
            if len(key) == 1 and key.isprintable():
                self.owner.state.picker_query += key
                self.owner.refresh_active_picker_matches(reset_selection=True)
                self.owner.state.dirty = True
            return True, False

        if key == "ENTER" or key_lower == "l":
            should_quit = self.owner.activate_picker_selection()
            if should_quit:
                return True, True
            self.owner.state.dirty = True
            return True, False
        if key == "UP" or key_lower == "k":
            self._move_selection(-1)
            return True, False
        if key == "DOWN" or key_lower == "j":
            self._move_selection(1)
            return True, False
        if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
            self._handle_mouse_wheel(key)
            return True, False
        if key.startswith("MOUSE_LEFT_DOWN:"):
            should_quit = self._handle_mouse_click(
                key,
                self.owner.visible_content_rows(),
                double_click_seconds,
                focus_query_row=True,
            )
            if should_quit:
                return True, True
            return True, False
        return True, False

    def resolve_symbol_target(self) -> Path | None:
        """Resolve file path whose symbols should populate the symbol picker."""
        if self.owner.state.current_path.is_file():
            return self.owner.state.current_path.resolve()
        if not self.owner.state.tree_entries:
            return None
        entry = self.owner.state.tree_entries[self.owner.state.selected_idx]
        if entry.is_dir or not entry.path.is_file():
            return None
        return entry.path.resolve()

    def open_symbol_picker(self) -> None:
        """Enter symbol-picker mode and populate symbols for current file target."""
        if not self.owner.state.picker_active:
            self.owner.state.picker_prev_browser_visible = self.owner.state.browser_visible
        self.owner.state.picker_active = True
        self.owner.state.picker_mode = "symbols"
        self.owner.state.picker_focus = "query"
        self.owner.state.picker_message = ""
        self.owner.state.picker_query = ""
        self.owner.state.picker_selected = 0
        self.owner.state.picker_list_start = 0
        self.owner.state.picker_matches = []
        self.owner.state.picker_match_labels = []
        self.owner.state.picker_match_lines = []
        self.owner.state.picker_match_commands = []
        self.owner.state.picker_command_ids = []
        self.owner.state.picker_command_labels = []
        was_browser_visible = self.owner.state.browser_visible
        self.owner.state.browser_visible = True
        if self.owner.state.wrap_text and not was_browser_visible:
            self.owner.rebuild_screen_lines()

        target = self.resolve_symbol_target()
        self.owner.state.picker_symbol_file = target
        self.owner.state.picker_symbol_labels = []
        self.owner.state.picker_symbol_lines = []
        if target is None:
            self.owner.state.picker_message = " no file selected"
            self.owner.state.dirty = True
            return

        symbols, error = collect_symbols(target)
        if error:
            self.owner.state.picker_message = f" {error}"
            self.owner.state.dirty = True
            return

        self.owner.state.picker_symbol_labels = [symbol.label for symbol in symbols]
        self.owner.state.picker_symbol_lines = [symbol.line for symbol in symbols]
        if not self.owner.state.picker_symbol_labels:
            self.owner.state.picker_message = " no functions/classes/imports found"
            self.owner.state.dirty = True
            return

        self.owner.refresh_symbol_picker_matches(reset_selection=True)
        self.owner.state.dirty = True

    def open_command_picker(self) -> None:
        """Enter command-palette mode and load command label/id lists."""
        if not self.owner.state.picker_active:
            self.owner.state.picker_prev_browser_visible = self.owner.state.browser_visible
        self.owner.state.picker_active = True
        self.owner.state.picker_mode = "commands"
        self.owner.state.picker_focus = "tree"
        self.owner.state.picker_message = ""
        self.owner.state.picker_query = ""
        self.owner.state.picker_selected = 0
        self.owner.state.picker_list_start = 0
        self.owner.state.picker_matches = []
        self.owner.state.picker_match_labels = []
        self.owner.state.picker_match_lines = []
        self.owner.state.picker_match_commands = []
        self.owner.state.picker_symbol_file = None
        self.owner.state.picker_symbol_labels = []
        self.owner.state.picker_symbol_lines = []
        self.owner.state.picker_command_ids = [
            command_id for command_id, _ in self.owner.command_palette_items
        ]
        self.owner.state.picker_command_labels = [label for _, label in self.owner.command_palette_items]
        was_browser_visible = self.owner.state.browser_visible
        self.owner.state.browser_visible = True
        if self.owner.state.wrap_text and not was_browser_visible:
            self.owner.rebuild_screen_lines()

        self.owner.refresh_command_picker_matches(reset_selection=True)
        self.owner.state.dirty = True

    def close_picker(self, reset_query: bool = True) -> None:
        """Close picker UI and restore non-picker browser visibility state."""
        previous_browser_visible = self.owner.state.picker_prev_browser_visible
        self.owner.state.picker_active = False
        if reset_query:
            self.owner.state.picker_query = ""
        self.owner.state.picker_mode = "symbols"
        self.owner.state.picker_focus = "query"
        self.owner.state.picker_message = ""
        self.owner.state.picker_selected = 0
        self.owner.state.picker_list_start = 0
        self.owner.state.picker_matches = []
        self.owner.state.picker_match_labels = []
        self.owner.state.picker_match_lines = []
        self.owner.state.picker_match_commands = []
        self.owner.state.picker_symbol_file = None
        self.owner.state.picker_symbol_labels = []
        self.owner.state.picker_symbol_lines = []
        self.owner.state.picker_command_ids = []
        self.owner.state.picker_command_labels = []
        self.owner.state.picker_prev_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = self.owner.state.browser_visible != previous_browser_visible
            self.owner.state.browser_visible = previous_browser_visible
            if self.owner.state.wrap_text and browser_visibility_changed:
                self.owner.rebuild_screen_lines()
        self.owner.state.dirty = True

    def activate_picker_selection(self) -> bool:
        """Activate current picker row for symbols or command palette actions."""
        if self.owner.state.picker_mode == "symbols" and self.owner.state.picker_match_lines:
            selected_line = self.owner.state.picker_match_lines[self.owner.state.picker_selected]
            symbol_file = self.owner.state.picker_symbol_file
            origin = self.owner.current_jump_location()
            self.close_picker()
            if symbol_file is not None and symbol_file.resolve() != self.owner.state.current_path.resolve():
                self.owner.jump_to_path(symbol_file.resolve())
            self.owner.jump_to_line(selected_line)
            self.owner.record_jump_if_changed(origin)
            return False
        if self.owner.state.picker_mode == "commands" and self.owner.state.picker_match_commands:
            command_id = self.owner.state.picker_match_commands[self.owner.state.picker_selected]
            self.close_picker()
            return self.owner.execute_command_palette_action(command_id)
        return False
