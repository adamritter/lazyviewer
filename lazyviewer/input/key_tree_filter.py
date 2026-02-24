"""Tree-filter-mode keyboard handling."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..runtime.state import AppState


@dataclass(frozen=True)
class TreeFilterKeyCallbacks:
    """External operations required for tree-filter key handling."""

    handle_tree_mouse_wheel: Callable[[str], bool]
    handle_tree_mouse_click: Callable[[str], bool]
    toggle_help_panel: Callable[[], None]
    close_tree_filter: Callable[..., None]
    activate_tree_filter_selection: Callable[[], None]
    move_tree_selection: Callable[[int], bool]
    apply_tree_filter_query: Callable[..., None]
    jump_to_next_content_hit: Callable[[int], bool]


def handle_tree_filter_key(
    key: str,
    state: AppState,
    callbacks: TreeFilterKeyCallbacks,
) -> bool:
    """Handle keys for tree filter prompt, list navigation, and hit jumps."""
    handle_tree_mouse_wheel = callbacks.handle_tree_mouse_wheel
    handle_tree_mouse_click = callbacks.handle_tree_mouse_click
    toggle_help_panel = callbacks.toggle_help_panel
    close_tree_filter = callbacks.close_tree_filter
    activate_tree_filter_selection = callbacks.activate_tree_filter_selection
    move_tree_selection = callbacks.move_tree_selection
    apply_tree_filter_query = callbacks.apply_tree_filter_query
    jump_to_next_content_hit = callbacks.jump_to_next_content_hit
    if not state.tree_filter_active:
        return False

    def apply_live_filter_query(query: str) -> None:
        """Apply query updates with mode-specific preview/selection semantics."""
        content_mode = state.tree_filter_mode == "content"
        apply_tree_filter_query(
            query,
            preview_selection=not content_mode,
            select_first_file=not content_mode,
        )

    if state.tree_filter_editing:
        if handle_tree_mouse_wheel(key):
            return True
        if handle_tree_mouse_click(key):
            return True
        if key == "ESC":
            close_tree_filter(
                clear_query=True,
                restore_origin=state.tree_filter_mode == "content",
            )
            return True
        if key == "ENTER":
            activate_tree_filter_selection()
            return True
        if key == "TAB":
            state.tree_filter_editing = False
            state.dirty = True
            return True
        if key == "UP" or key == "CTRL_K":
            if move_tree_selection(-1):
                state.dirty = True
            return True
        if key == "DOWN" or key == "CTRL_J":
            if move_tree_selection(1):
                state.dirty = True
            return True
        if key == "BACKSPACE":
            if state.tree_filter_query:
                apply_live_filter_query(state.tree_filter_query[:-1])
            return True
        if key == "CTRL_U":
            if state.tree_filter_query:
                apply_live_filter_query("")
            return True
        if key == "CTRL_QUESTION":
            toggle_help_panel()
            return True
        if len(key) == 1 and key.isprintable():
            apply_live_filter_query(state.tree_filter_query + key)
            return True
        return True

    if key == "TAB":
        state.tree_filter_editing = True
        state.dirty = True
        return True
    if key == "ENTER":
        activate_tree_filter_selection()
        return True
    if key == "ESC":
        close_tree_filter(clear_query=True)
        return True
    if state.tree_filter_mode == "content":
        if key == "n":
            if jump_to_next_content_hit(1):
                state.dirty = True
            return True
        if key in {"N", "p"}:
            if jump_to_next_content_hit(-1):
                state.dirty = True
            return True
    return False
