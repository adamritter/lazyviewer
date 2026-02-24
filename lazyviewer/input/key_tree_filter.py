"""Tree-filter-mode keyboard handling compatibility wrapper."""

from __future__ import annotations

from collections.abc import Callable

from ..runtime.state import AppState
from ..tree_pane.panels.filter.panel import FilterPanel


class _TreeFilterPanelAdapter:
    """Adapter exposing FilterPanel owner API over callback functions."""

    def __init__(
        self,
        *,
        state: AppState,
        close_tree_filter: Callable[..., None],
        activate_tree_filter_selection: Callable[[], None],
        move_tree_selection: Callable[[int], bool],
        apply_tree_filter_query: Callable[..., None],
        jump_to_next_content_hit: Callable[[int], bool],
    ) -> None:
        self.state = state
        self._close_tree_filter = close_tree_filter
        self._activate_tree_filter_selection = activate_tree_filter_selection
        self._move_tree_selection = move_tree_selection
        self._apply_tree_filter_query = apply_tree_filter_query
        self._jump_to_next_content_hit = jump_to_next_content_hit

    def close_tree_filter(self, clear_query: bool = True, restore_origin: bool = False) -> None:
        self._close_tree_filter(clear_query=clear_query, restore_origin=restore_origin)

    def activate_tree_filter_selection(self) -> None:
        self._activate_tree_filter_selection()

    def move_tree_selection(self, direction: int) -> bool:
        return self._move_tree_selection(direction)

    def apply_tree_filter_query(
        self,
        query: str,
        preview_selection: bool = True,
        select_first_file: bool = True,
    ) -> None:
        self._apply_tree_filter_query(
            query,
            preview_selection=preview_selection,
            select_first_file=select_first_file,
        )

    def jump_to_next_content_hit(self, direction: int) -> bool:
        return self._jump_to_next_content_hit(direction)


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
    adapter = _TreeFilterPanelAdapter(
        state=state,
        close_tree_filter=close_tree_filter,
        activate_tree_filter_selection=activate_tree_filter_selection,
        move_tree_selection=move_tree_selection,
        apply_tree_filter_query=apply_tree_filter_query,
        jump_to_next_content_hit=jump_to_next_content_hit,
    )
    return FilterPanel(adapter).handle_key(
        key,
        handle_tree_mouse_wheel=handle_tree_mouse_wheel,
        handle_tree_mouse_click=handle_tree_mouse_click,
        toggle_help_panel=toggle_help_panel,
    )
