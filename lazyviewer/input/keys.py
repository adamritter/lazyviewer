"""Keyboard dispatch facade for picker, tree-filter, and normal modes."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

from ..runtime.state import AppState
from ..tree_pane.panels.filter.panel import FilterPanel
from ..tree_pane.panels.picker.panel import PickerPanel
from .key_normal import NormalKeyContext, NormalKeyHandler, handle_normal_key
from .key_registry import KeyComboBinding, KeyComboRegistry

__all__ = [
    "KeyComboBinding",
    "KeyComboRegistry",
    "NormalKeyContext",
    "NormalKeyHandler",
    "handle_picker_key",
    "handle_tree_filter_key",
    "handle_normal_key",
]


def handle_picker_key(
    key: str,
    state: AppState,
    double_click_seconds: float,
    *,
    close_picker: Callable[[], None],
    refresh_command_picker_matches: Callable[..., None],
    activate_picker_selection: Callable[[], bool],
    visible_content_rows: Callable[[], int],
    refresh_active_picker_matches: Callable[..., None],
) -> tuple[bool, bool]:
    """Handle one key while picker is active."""
    owner = SimpleNamespace(
        state=state,
        close_picker=close_picker,
        refresh_command_picker_matches=refresh_command_picker_matches,
        activate_picker_selection=activate_picker_selection,
        visible_content_rows=visible_content_rows,
        refresh_active_picker_matches=refresh_active_picker_matches,
    )
    return PickerPanel(owner).handle_key(key, double_click_seconds)


def handle_tree_filter_key(
    key: str,
    state: AppState,
    *,
    handle_tree_mouse_wheel: Callable[[str], bool],
    handle_tree_mouse_click: Callable[[str], bool],
    toggle_help_panel: Callable[[], None],
    close_tree_filter: Callable[..., None],
    activate_tree_filter_selection: Callable[[], None],
    move_tree_selection: Callable[[int], bool],
    apply_tree_filter_query: Callable[..., None],
    jump_to_next_content_hit: Callable[[int], bool],
) -> bool:
    """Handle keys for tree filter prompt, list navigation, and hit jumps."""
    owner = SimpleNamespace(
        state=state,
        close_tree_filter=close_tree_filter,
        activate_tree_filter_selection=activate_tree_filter_selection,
        move_tree_selection=move_tree_selection,
        apply_tree_filter_query=apply_tree_filter_query,
        jump_to_next_content_hit=jump_to_next_content_hit,
    )
    return FilterPanel(owner).handle_key(
        key,
        handle_tree_mouse_wheel=handle_tree_mouse_wheel,
        handle_tree_mouse_click=handle_tree_mouse_click,
        toggle_help_panel=toggle_help_panel,
    )
