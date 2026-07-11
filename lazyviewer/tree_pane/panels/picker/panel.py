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
        if not self.owner.state.picker.match_labels:
            return
        previous = self.owner.state.picker.selected
        self.owner.state.picker.selected = max(
            0,
            min(len(self.owner.state.picker.match_labels) - 1, self.owner.state.picker.selected + direction),
        )
        if self.owner.state.picker.selected != previous:
            self.owner.state.interface.dirty = True

    def _handle_mouse_wheel(self, mouse_key: str) -> None:
        direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
        col, _row = self._parse_mouse_col_row(mouse_key)
        if self.owner.state.layout.browser_visible and col is not None and col <= self.owner.state.layout.left_width:
            self._move_selection(direction)
            return
        previous_start = self.owner.state.preview.scroll
        self.owner.state.preview.scroll += direction * 3
        self.owner.state.preview.scroll = max(0, min(self.owner.state.preview.scroll, self.owner.state.preview.max_scroll))
        if self.owner.state.preview.scroll != previous_start:
            self.owner.state.interface.dirty = True

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
            self.owner.state.layout.browser_visible
            and col is not None
            and row is not None
            and 1 <= row <= visible_rows
            and col <= self.owner.state.layout.left_width
        ):
            return False
        if row == 1:
            if focus_query_row:
                self.owner.state.picker.focus = "query"
                self.owner.state.interface.dirty = True
            return False
        clicked_idx = self.owner.state.picker.list_start + (row - 2)
        if not (0 <= clicked_idx < len(self.owner.state.picker.match_labels)):
            return False
        previous = self.owner.state.picker.selected
        self.owner.state.picker.selected = clicked_idx
        if self.owner.state.picker.selected != previous:
            self.owner.state.interface.dirty = True
        now = time.monotonic()
        is_double = clicked_idx == self.owner.state.interface.last_click_index and (
            now - self.owner.state.interface.last_click_time
        ) <= double_click_seconds
        self.owner.state.interface.last_click_index = clicked_idx
        self.owner.state.interface.last_click_time = now
        if not is_double:
            return False
        should_quit = self.owner.activate_picker_selection()
        if should_quit:
            return True
        self.owner.state.interface.dirty = True
        return False

    def handle_key(self, key: str, double_click_seconds: float) -> tuple[bool, bool]:
        """Handle one key while picker is active."""
        key_lower = key.lower()

        if not self.owner.state.picker.active:
            return False, False

        if key == "ESC" or key == "\x03":
            self.owner.close_picker()
            return True, False

        if self.owner.state.picker.mode == "commands":
            if key == "UP" or key_lower == "k":
                self._move_selection(-1)
                return True, False
            if key == "DOWN" or key_lower == "j":
                self._move_selection(1)
                return True, False
            if key == "BACKSPACE":
                if self.owner.state.picker.query:
                    self.owner.state.picker.query = self.owner.state.picker.query[:-1]
                    self.owner.refresh_command_picker_matches(reset_selection=True)
                    self.owner.state.interface.dirty = True
                return True, False
            if len(key) == 1 and key.isprintable():
                self.owner.state.picker.query += key
                self.owner.refresh_command_picker_matches(reset_selection=True)
                self.owner.state.interface.dirty = True
                return True, False
            if key == "ENTER" or key_lower == "l":
                should_quit = self.owner.activate_picker_selection()
                if should_quit:
                    return True, True
                self.owner.state.interface.dirty = True
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
            self.owner.state.picker.focus = "tree" if self.owner.state.picker.focus == "query" else "query"
            self.owner.state.interface.dirty = True
            return True, False

        if self.owner.state.picker.focus == "query":
            if key == "ENTER":
                self.owner.state.picker.focus = "tree"
                self.owner.state.interface.dirty = True
                return True, False
            if key == "BACKSPACE":
                if self.owner.state.picker.query:
                    self.owner.state.picker.query = self.owner.state.picker.query[:-1]
                    self.owner.refresh_active_picker_matches(reset_selection=True)
                    self.owner.state.interface.dirty = True
                return True, False
            if len(key) == 1 and key.isprintable():
                self.owner.state.picker.query += key
                self.owner.refresh_active_picker_matches(reset_selection=True)
                self.owner.state.interface.dirty = True
            return True, False

        if key == "ENTER" or key_lower == "l":
            should_quit = self.owner.activate_picker_selection()
            if should_quit:
                return True, True
            self.owner.state.interface.dirty = True
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
        if self.owner.state.workspace.current_path.is_file():
            return self.owner.state.workspace.current_path.resolve()
        if not self.owner.state.workspace.entries:
            return None
        entry = self.owner.state.workspace.entries[self.owner.state.workspace.selected]
        if entry.is_dir or not entry.path.is_file():
            return None
        return entry.path.resolve()

    def open_symbol_picker(self) -> None:
        """Enter symbol-picker mode and populate symbols for current file target."""
        if not self.owner.state.picker.active:
            self.owner.state.picker.previous_browser_visible = self.owner.state.layout.browser_visible
        self.owner.state.picker.active = True
        self.owner.state.picker.mode = "symbols"
        self.owner.state.picker.focus = "query"
        self.owner.state.picker.message = ""
        self.owner.state.picker.query = ""
        self.owner.state.picker.selected = 0
        self.owner.state.picker.list_start = 0
        self.owner.state.picker.matches = []
        self.owner.state.picker.match_labels = []
        self.owner.state.picker.match_lines = []
        self.owner.state.picker.match_commands = []
        self.owner.state.picker.command_ids = []
        self.owner.state.picker.command_labels = []
        was_browser_visible = self.owner.state.layout.browser_visible
        self.owner.state.layout.browser_visible = True
        if self.owner.state.preview.wrap and not was_browser_visible:
            self.owner.rebuild_screen_lines()

        target = self.resolve_symbol_target()
        self.owner.state.picker.symbol_file = target
        self.owner.state.picker.symbol_labels = []
        self.owner.state.picker.symbol_lines = []
        if target is None:
            self.owner.state.picker.message = " no file selected"
            self.owner.state.interface.dirty = True
            return

        symbols, error = collect_symbols(target)
        if error:
            self.owner.state.picker.message = f" {error}"
            self.owner.state.interface.dirty = True
            return

        self.owner.state.picker.symbol_labels = [symbol.label for symbol in symbols]
        self.owner.state.picker.symbol_lines = [symbol.line for symbol in symbols]
        if not self.owner.state.picker.symbol_labels:
            self.owner.state.picker.message = " no functions/classes/imports found"
            self.owner.state.interface.dirty = True
            return

        self.owner.refresh_symbol_picker_matches(reset_selection=True)
        self.owner.state.interface.dirty = True

    def open_command_picker(self) -> None:
        """Enter command-palette mode and load command label/id lists."""
        if not self.owner.state.picker.active:
            self.owner.state.picker.previous_browser_visible = self.owner.state.layout.browser_visible
        self.owner.state.picker.active = True
        self.owner.state.picker.mode = "commands"
        self.owner.state.picker.focus = "tree"
        self.owner.state.picker.message = ""
        self.owner.state.picker.query = ""
        self.owner.state.picker.selected = 0
        self.owner.state.picker.list_start = 0
        self.owner.state.picker.matches = []
        self.owner.state.picker.match_labels = []
        self.owner.state.picker.match_lines = []
        self.owner.state.picker.match_commands = []
        self.owner.state.picker.symbol_file = None
        self.owner.state.picker.symbol_labels = []
        self.owner.state.picker.symbol_lines = []
        self.owner.state.picker.command_ids = [
            command_id for command_id, _ in self.owner.command_palette_items
        ]
        self.owner.state.picker.command_labels = [label for _, label in self.owner.command_palette_items]
        was_browser_visible = self.owner.state.layout.browser_visible
        self.owner.state.layout.browser_visible = True
        if self.owner.state.preview.wrap and not was_browser_visible:
            self.owner.rebuild_screen_lines()

        self.owner.refresh_command_picker_matches(reset_selection=True)
        self.owner.state.interface.dirty = True

    def close_picker(self, reset_query: bool = True) -> None:
        """Close picker UI and restore non-picker browser visibility state."""
        previous_browser_visible = self.owner.state.picker.previous_browser_visible
        self.owner.state.picker.active = False
        if reset_query:
            self.owner.state.picker.query = ""
        self.owner.state.picker.mode = "symbols"
        self.owner.state.picker.focus = "query"
        self.owner.state.picker.message = ""
        self.owner.state.picker.selected = 0
        self.owner.state.picker.list_start = 0
        self.owner.state.picker.matches = []
        self.owner.state.picker.match_labels = []
        self.owner.state.picker.match_lines = []
        self.owner.state.picker.match_commands = []
        self.owner.state.picker.symbol_file = None
        self.owner.state.picker.symbol_labels = []
        self.owner.state.picker.symbol_lines = []
        self.owner.state.picker.command_ids = []
        self.owner.state.picker.command_labels = []
        self.owner.state.picker.previous_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = self.owner.state.layout.browser_visible != previous_browser_visible
            self.owner.state.layout.browser_visible = previous_browser_visible
            if self.owner.state.preview.wrap and browser_visibility_changed:
                self.owner.rebuild_screen_lines()
        self.owner.state.interface.dirty = True

    def activate_picker_selection(self) -> bool:
        """Activate current picker row for symbols or command palette actions."""
        if self.owner.state.picker.mode == "symbols" and self.owner.state.picker.match_lines:
            selected_line = self.owner.state.picker.match_lines[self.owner.state.picker.selected]
            symbol_file = self.owner.state.picker.symbol_file
            origin = self.owner.current_jump_location()
            self.close_picker()
            if symbol_file is not None and symbol_file.resolve() != self.owner.state.workspace.current_path.resolve():
                self.owner.jump_to_path(symbol_file.resolve())
            self.owner.jump_to_line(selected_line)
            self.owner.record_jump_if_changed(origin)
            return False
        if self.owner.state.picker.mode == "commands" and self.owner.state.picker.match_commands:
            command_id = self.owner.state.picker.match_commands[self.owner.state.picker.selected]
            self.close_picker()
            return self.owner.execute_command_palette_action(command_id)
        return False
