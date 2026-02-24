"""Tree-filter lifecycle helpers for :mod:`tree_pane.panels.filter`."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .controller import TreeFilterController
    from ....runtime.navigation import JumpLocation


def open_tree_filter(controller: TreeFilterController, mode: str = "files") -> None:
    """Open filter panel in requested mode and initialize session fields."""
    was_active = controller.state.tree_filter_active
    previous_mode = controller.state.tree_filter_mode
    if not controller.state.tree_filter_active:
        controller.state.tree_filter_prev_browser_visible = controller.state.browser_visible
    was_browser_visible = controller.state.browser_visible
    controller.state.browser_visible = True
    if controller.state.wrap_text and not was_browser_visible:
        controller.rebuild_screen_lines()
    controller.state.tree_filter_active = True
    controller.state.tree_filter_mode = mode
    controller.state.tree_filter_editing = True
    controller.state.tree_filter_origin = controller.current_jump_location() if mode == "content" else None
    controller.state.tree_filter_query = ""
    controller.state.tree_filter_match_count = 0
    controller.state.tree_filter_truncated = False
    controller.reset_tree_filter_session_state()
    if was_active and previous_mode != mode:
        controller.rebuild_tree_entries(preferred_path=controller.state.current_path.resolve())
    controller.state.dirty = True
    if controller.on_tree_filter_state_change is not None:
        controller.on_tree_filter_state_change()


def close_tree_filter(
    controller: TreeFilterController,
    clear_query: bool = True,
    restore_origin: bool = False,
) -> None:
    """Close filter panel, optionally restoring original content-search position."""
    previous_browser_visible = controller.state.tree_filter_prev_browser_visible
    restore_location: JumpLocation | None = None
    if (
        restore_origin
        and controller.state.tree_filter_mode == "content"
        and controller.state.tree_filter_origin is not None
    ):
        restore_location = controller.state.tree_filter_origin.normalized()
    controller.state.tree_filter_active = False
    controller.state.tree_filter_editing = False
    controller.state.tree_filter_mode = "files"
    if clear_query:
        controller.state.tree_filter_query = ""
        controller.state.tree_filter_truncated = False
    controller.reset_tree_filter_session_state()
    controller.state.tree_filter_prev_browser_visible = None
    if previous_browser_visible is not None:
        browser_visibility_changed = controller.state.browser_visible != previous_browser_visible
        controller.state.browser_visible = previous_browser_visible
        if controller.state.wrap_text and browser_visibility_changed:
            controller.rebuild_screen_lines()
    if restore_location is not None:
        controller.jump_to_path(restore_location.path)
        controller.state.max_start = max(0, len(controller.state.lines) - controller.visible_content_rows())
        controller.state.start = max(0, min(restore_location.start, controller.state.max_start))
        controller.state.text_x = 0 if controller.state.wrap_text else max(0, restore_location.text_x)
    else:
        controller.rebuild_tree_entries(preferred_path=controller.state.current_path.resolve())
    controller.state.tree_filter_origin = None
    controller.state.dirty = True
    if controller.on_tree_filter_state_change is not None:
        controller.on_tree_filter_state_change()


def activate_tree_filter_selection(controller: TreeFilterController) -> None:
    """Activate selected filter result according to current filter mode."""
    if not controller.state.tree_entries:
        if controller.state.tree_filter_mode == "content":
            controller.state.tree_filter_editing = False
            controller.state.dirty = True
        else:
            close_tree_filter(controller, clear_query=True)
        return

    entry = controller.state.tree_entries[controller.state.selected_idx]
    if entry.is_dir:
        candidate_idx = controller.nearest_tree_filter_result_entry_index(controller.state.selected_idx)
        if candidate_idx is None:
            close_tree_filter(controller, clear_query=True)
            return
        controller.state.selected_idx = candidate_idx
        entry = controller.state.tree_entries[controller.state.selected_idx]

    selected_path = entry.path.resolve()
    selected_line = entry.line if entry.kind == "search_hit" else None
    if controller.state.tree_filter_mode == "content":
        origin = controller.current_jump_location()
        controller.state.tree_filter_editing = False
        controller.preview_selected_entry()
        controller.record_jump_if_changed(origin)
        controller.state.dirty = True
        return

    origin = controller.current_jump_location()
    close_tree_filter(controller, clear_query=True)
    controller.jump_to_path(selected_path)
    if selected_line is not None:
        controller.jump_to_line(max(0, selected_line - 1))
    controller.record_jump_if_changed(origin)
    controller.state.dirty = True
