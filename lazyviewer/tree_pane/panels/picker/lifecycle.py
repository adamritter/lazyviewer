"""Picker lifecycle helpers for :mod:`tree_pane.panels.picker`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ....source_pane.symbols import collect_symbols

if TYPE_CHECKING:
    from .controller import NavigationController


def resolve_symbol_target(controller: NavigationController) -> Path | None:
    """Resolve file path whose symbols should populate the symbol picker."""
    if controller.state.current_path.is_file():
        return controller.state.current_path.resolve()
    if not controller.state.tree_entries:
        return None
    entry = controller.state.tree_entries[controller.state.selected_idx]
    if entry.is_dir or not entry.path.is_file():
        return None
    return entry.path.resolve()


def open_symbol_picker(controller: NavigationController) -> None:
    """Enter symbol-picker mode and populate symbols for current file target."""
    if not controller.state.picker_active:
        controller.state.picker_prev_browser_visible = controller.state.browser_visible
    controller.state.picker_active = True
    controller.state.picker_mode = "symbols"
    controller.state.picker_focus = "query"
    controller.state.picker_message = ""
    controller.state.picker_query = ""
    controller.state.picker_selected = 0
    controller.state.picker_list_start = 0
    controller.state.picker_matches = []
    controller.state.picker_match_labels = []
    controller.state.picker_match_lines = []
    controller.state.picker_match_commands = []
    controller.state.picker_command_ids = []
    controller.state.picker_command_labels = []
    was_browser_visible = controller.state.browser_visible
    controller.state.browser_visible = True
    if controller.state.wrap_text and not was_browser_visible:
        controller.rebuild_screen_lines()

    target = resolve_symbol_target(controller)
    controller.state.picker_symbol_file = target
    controller.state.picker_symbol_labels = []
    controller.state.picker_symbol_lines = []
    if target is None:
        controller.state.picker_message = " no file selected"
        controller.state.dirty = True
        return

    symbols, error = collect_symbols(target)
    if error:
        controller.state.picker_message = f" {error}"
        controller.state.dirty = True
        return

    controller.state.picker_symbol_labels = [symbol.label for symbol in symbols]
    controller.state.picker_symbol_lines = [symbol.line for symbol in symbols]
    if not controller.state.picker_symbol_labels:
        controller.state.picker_message = " no functions/classes/imports found"
        controller.state.dirty = True
        return

    controller.refresh_symbol_picker_matches(reset_selection=True)
    controller.state.dirty = True


def open_command_picker(controller: NavigationController) -> None:
    """Enter command-palette mode and load command label/id lists."""
    if not controller.state.picker_active:
        controller.state.picker_prev_browser_visible = controller.state.browser_visible
    controller.state.picker_active = True
    controller.state.picker_mode = "commands"
    controller.state.picker_focus = "tree"
    controller.state.picker_message = ""
    controller.state.picker_query = ""
    controller.state.picker_selected = 0
    controller.state.picker_list_start = 0
    controller.state.picker_matches = []
    controller.state.picker_match_labels = []
    controller.state.picker_match_lines = []
    controller.state.picker_match_commands = []
    controller.state.picker_symbol_file = None
    controller.state.picker_symbol_labels = []
    controller.state.picker_symbol_lines = []
    controller.state.picker_command_ids = [command_id for command_id, _ in controller.command_palette_items]
    controller.state.picker_command_labels = [label for _, label in controller.command_palette_items]
    was_browser_visible = controller.state.browser_visible
    controller.state.browser_visible = True
    if controller.state.wrap_text and not was_browser_visible:
        controller.rebuild_screen_lines()

    controller.refresh_command_picker_matches(reset_selection=True)
    controller.state.dirty = True


def close_picker(controller: NavigationController, reset_query: bool = True) -> None:
    """Close picker UI and restore non-picker browser visibility state."""
    previous_browser_visible = controller.state.picker_prev_browser_visible
    controller.state.picker_active = False
    if reset_query:
        controller.state.picker_query = ""
    controller.state.picker_mode = "symbols"
    controller.state.picker_focus = "query"
    controller.state.picker_message = ""
    controller.state.picker_selected = 0
    controller.state.picker_list_start = 0
    controller.state.picker_matches = []
    controller.state.picker_match_labels = []
    controller.state.picker_match_lines = []
    controller.state.picker_match_commands = []
    controller.state.picker_symbol_file = None
    controller.state.picker_symbol_labels = []
    controller.state.picker_symbol_lines = []
    controller.state.picker_command_ids = []
    controller.state.picker_command_labels = []
    controller.state.picker_prev_browser_visible = None
    if previous_browser_visible is not None:
        browser_visibility_changed = controller.state.browser_visible != previous_browser_visible
        controller.state.browser_visible = previous_browser_visible
        if controller.state.wrap_text and browser_visibility_changed:
            controller.rebuild_screen_lines()
    controller.state.dirty = True


def activate_picker_selection(controller: NavigationController) -> bool:
    """Activate current picker row for symbols or command palette actions."""
    if controller.state.picker_mode == "symbols" and controller.state.picker_match_lines:
        selected_line = controller.state.picker_match_lines[controller.state.picker_selected]
        symbol_file = controller.state.picker_symbol_file
        origin = controller.current_jump_location()
        close_picker(controller)
        if symbol_file is not None and symbol_file.resolve() != controller.state.current_path.resolve():
            controller.jump_to_path(symbol_file.resolve())
        controller.jump_to_line(selected_line)
        controller.record_jump_if_changed(origin)
        return False
    if controller.state.picker_mode == "commands" and controller.state.picker_match_commands:
        command_id = controller.state.picker_match_commands[controller.state.picker_selected]
        close_picker(controller)
        return controller.execute_command_palette_action(command_id)
    return False
