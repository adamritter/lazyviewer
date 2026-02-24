"""Tree pane runtime façade used by the application layer."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..input.mouse import TreeMouseHandlers
from ..runtime.navigation import JumpLocation
from ..runtime.state import AppState
from .panels.filter import TreeFilterOps
from .panels.picker import NavigationPickerOps


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
        self.filter = TreeFilterOps(
            state=state,
            visible_content_rows=visible_content_rows,
            rebuild_screen_lines=rebuild_screen_lines,
            preview_selected_entry=preview_selected_entry,
            current_jump_location=self._current_jump_location,
            record_jump_if_changed=self._record_jump_if_changed,
            jump_to_path=self._jump_to_path,
            jump_to_line=self._jump_to_line,
            on_tree_filter_state_change=on_tree_filter_state_change,
        )
        self.navigation = NavigationPickerOps(
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
        )
        self.navigation.set_open_tree_filter(self.filter.open_tree_filter)
        self.mouse = mouse_handlers

    def attach_mouse(self, mouse_handlers: TreeMouseHandlers) -> None:
        """Attach mouse handler implementation once constructed."""
        self.mouse = mouse_handlers

    def _current_jump_location(self):
        return self.navigation.current_jump_location()

    def _record_jump_if_changed(self, origin: JumpLocation) -> None:
        self.navigation.record_jump_if_changed(origin)

    def _jump_to_path(self, target: Path) -> None:
        self.navigation.jump_to_path(target)

    def _jump_to_line(self, line_number: int) -> None:
        self.navigation.jump_to_line(line_number)

    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        if self.mouse is None:
            return False
        return self.mouse.handle_tree_mouse_click(mouse_key)

    def tick_source_selection_drag(self) -> None:
        if self.mouse is None:
            return
        self.mouse.tick_source_selection_drag()
