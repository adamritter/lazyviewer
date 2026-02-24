"""Tree pane runtime façade used by the application layer."""

from __future__ import annotations

from collections.abc import Callable

from ..input.mouse import TreeMouseHandlers
from ..runtime.state import AppState
from .panels.filter import TreeFilterController
from .panels.picker import NavigationController


class TreePane:
    """App-owned tree pane object exposing filter, navigation, and mouse ops."""

    def __init__(
        self,
        *,
        state: AppState,
        command_palette_items: tuple[tuple[str, str], ...],
        visible_content_rows: Callable[[], int],
        rebuild_screen_lines: Callable[..., None],
        preview_selected_entry: Callable[..., None],
        schedule_tree_filter_index_warmup: Callable[[], None],
        mark_tree_watch_dirty: Callable[[], None],
        reset_git_watch_context: Callable[[], None],
        refresh_git_status_overlay: Callable[..., None],
        refresh_rendered_for_current_path: Callable[..., None],
        on_tree_filter_state_change: Callable[[], None] | None = None,
        mouse_handlers: TreeMouseHandlers | None = None,
    ) -> None:
        self.filter = TreeFilterController(
            state=state,
            visible_content_rows=visible_content_rows,
            rebuild_screen_lines=rebuild_screen_lines,
            preview_selected_entry=preview_selected_entry,
            current_jump_location=lambda: self.navigation.current_jump_location(),
            record_jump_if_changed=lambda origin: self.navigation.record_jump_if_changed(origin),
            jump_to_path=lambda target: self.navigation.jump_to_path(target),
            jump_to_line=lambda line_number: self.navigation.jump_to_line(line_number),
            on_tree_filter_state_change=on_tree_filter_state_change,
        )
        self.navigation = NavigationController(
            state=state,
            command_palette_items=command_palette_items,
            rebuild_screen_lines=rebuild_screen_lines,
            rebuild_tree_entries=self.filter.rebuild_tree_entries,
            preview_selected_entry=preview_selected_entry,
            schedule_tree_filter_index_warmup=schedule_tree_filter_index_warmup,
            mark_tree_watch_dirty=mark_tree_watch_dirty,
            reset_git_watch_context=reset_git_watch_context,
            refresh_git_status_overlay=refresh_git_status_overlay,
            visible_content_rows=visible_content_rows,
            refresh_rendered_for_current_path=refresh_rendered_for_current_path,
            open_tree_filter=self.filter.open_tree_filter,
        )
        self.mouse = mouse_handlers

    def attach_mouse(self, mouse_handlers: TreeMouseHandlers) -> None:
        """Attach mouse handler implementation once constructed."""
        self.mouse = mouse_handlers

    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        if self.mouse is None:
            return False
        return self.mouse.handle_tree_mouse_click(mouse_key)

    def tick_source_selection_drag(self) -> None:
        if self.mouse is None:
            return
        self.mouse.tick_source_selection_drag()
