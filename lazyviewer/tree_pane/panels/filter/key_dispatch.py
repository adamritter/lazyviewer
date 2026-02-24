"""Tree-filter key dispatch helpers for :mod:`tree_pane.panels.filter`."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .controller import TreeFilterController


def toggle_tree_filter_mode(controller: TreeFilterController, mode: str) -> None:
    """Open/switch/close tree filter UI based on current editing state."""
    if controller.state.tree_filter_active:
        if controller.state.tree_filter_mode == mode and controller.state.tree_filter_editing:
            controller.close_tree_filter(clear_query=True)
        elif controller.state.tree_filter_mode != mode:
            controller.open_tree_filter(mode)
        else:
            controller.state.tree_filter_editing = True
            controller.state.dirty = True
        return
    controller.open_tree_filter(mode)


def handle_tree_filter_key(
    controller: TreeFilterController,
    key: str,
    *,
    handle_tree_mouse_wheel: Callable[[str], bool],
    handle_tree_mouse_click: Callable[[str], bool],
    toggle_help_panel: Callable[[], None],
) -> bool:
    """Handle one key for tree-filter prompt, list navigation, and hit jumps."""
    if not controller.state.tree_filter_active:
        return False

    def apply_live_filter_query(query: str) -> None:
        content_mode = controller.state.tree_filter_mode == "content"
        controller.apply_tree_filter_query(
            query,
            preview_selection=not content_mode,
            select_first_file=not content_mode,
        )

    if controller.state.tree_filter_editing:
        if handle_tree_mouse_wheel(key):
            return True
        if handle_tree_mouse_click(key):
            return True
        if key == "ESC":
            controller.close_tree_filter(
                clear_query=True,
                restore_origin=controller.state.tree_filter_mode == "content",
            )
            return True
        if key == "ENTER":
            controller.activate_tree_filter_selection()
            return True
        if key == "TAB":
            controller.state.tree_filter_editing = False
            controller.state.dirty = True
            return True
        if key == "UP" or key == "CTRL_K":
            if controller.move_tree_selection(-1):
                controller.state.dirty = True
            return True
        if key == "DOWN" or key == "CTRL_J":
            if controller.move_tree_selection(1):
                controller.state.dirty = True
            return True
        if key == "BACKSPACE":
            if controller.state.tree_filter_query:
                apply_live_filter_query(controller.state.tree_filter_query[:-1])
            return True
        if key == "CTRL_U":
            if controller.state.tree_filter_query:
                apply_live_filter_query("")
            return True
        if key == "CTRL_QUESTION":
            toggle_help_panel()
            return True
        if len(key) == 1 and key.isprintable():
            apply_live_filter_query(controller.state.tree_filter_query + key)
            return True
        return True

    if key == "TAB":
        controller.state.tree_filter_editing = True
        controller.state.dirty = True
        return True
    if key == "ENTER":
        controller.activate_tree_filter_selection()
        return True
    if key == "ESC":
        controller.close_tree_filter(clear_query=True)
        return True
    if controller.state.tree_filter_mode == "content":
        if key == "n":
            if controller.jump_to_next_content_hit(1):
                controller.state.dirty = True
            return True
        if key in {"N", "p"}:
            if controller.jump_to_next_content_hit(-1):
                controller.state.dirty = True
            return True
    return False
