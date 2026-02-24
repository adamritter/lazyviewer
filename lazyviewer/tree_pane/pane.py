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
    """App-owned tree pane object exposing filter/picker/mouse operations."""

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
            current_jump_location=self.current_jump_location,
            record_jump_if_changed=self.record_jump_if_changed,
            jump_to_path=self.jump_to_path,
            jump_to_line=self.jump_to_line,
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
        self.navigation.set_open_tree_filter(self.open_tree_filter)
        self.mouse = mouse_handlers

    def attach_mouse(self, mouse_handlers: TreeMouseHandlers) -> None:
        """Attach mouse handler implementation once constructed."""
        self.mouse = mouse_handlers

    # filter callbacks
    def get_tree_filter_loading_until(self) -> float:
        return self.filter.get_loading_until()

    def tree_view_rows(self) -> int:
        return self.filter.tree_view_rows()

    def tree_filter_prompt_prefix(self) -> str:
        return self.filter.tree_filter_prompt_prefix()

    def tree_filter_placeholder(self) -> str:
        return self.filter.tree_filter_placeholder()

    def open_tree_filter(self, mode: str) -> None:
        self.filter.open_tree_filter(mode)

    def close_tree_filter(self, *args, **kwargs) -> None:
        self.filter.close_tree_filter(*args, **kwargs)

    def activate_tree_filter_selection(self) -> None:
        self.filter.activate_tree_filter_selection()

    def move_tree_selection(self, direction: int) -> bool:
        return self.filter.move_tree_selection(direction)

    def apply_tree_filter_query(self, *args, **kwargs) -> None:
        self.filter.apply_tree_filter_query(*args, **kwargs)

    def jump_to_next_content_hit(self, direction: int) -> bool:
        return self.filter.jump_to_next_content_hit(direction)

    def coerce_tree_filter_result_index(self, idx: int) -> int | None:
        return self.filter.coerce_tree_filter_result_index(idx)

    def rebuild_tree_entries(self, *args, **kwargs) -> None:
        self.filter.rebuild_tree_entries(*args, **kwargs)

    # picker/navigation callbacks
    def open_symbol_picker(self) -> None:
        self.navigation.open_symbol_picker()

    def open_command_picker(self) -> None:
        self.navigation.open_command_picker()

    def close_picker(self, *args, **kwargs) -> None:
        self.navigation.close_picker(*args, **kwargs)

    def refresh_command_picker_matches(self, *args, **kwargs) -> None:
        self.navigation.refresh_command_picker_matches(*args, **kwargs)

    def activate_picker_selection(self) -> bool:
        return self.navigation.activate_picker_selection()

    def refresh_active_picker_matches(self, *args, **kwargs) -> None:
        self.navigation.refresh_active_picker_matches(*args, **kwargs)

    def reroot_to_parent(self) -> None:
        self.navigation.reroot_to_parent()

    def reroot_to_selected_target(self) -> None:
        self.navigation.reroot_to_selected_target()

    def toggle_hidden_files(self) -> None:
        self.navigation.toggle_hidden_files()

    def toggle_tree_pane(self) -> None:
        self.navigation.toggle_tree_pane()

    def toggle_wrap_mode(self) -> None:
        self.navigation.toggle_wrap_mode()

    def toggle_help_panel(self) -> None:
        self.navigation.toggle_help_panel()

    def set_named_mark(self, key: str) -> bool:
        return self.navigation.set_named_mark(key)

    def jump_to_named_mark(self, key: str) -> bool:
        return self.navigation.jump_to_named_mark(key)

    def current_jump_location(self):
        return self.navigation.current_jump_location()

    def record_jump_if_changed(self, origin: JumpLocation) -> None:
        self.navigation.record_jump_if_changed(origin)

    def jump_back_in_history(self) -> bool:
        return self.navigation.jump_back_in_history()

    def jump_forward_in_history(self) -> bool:
        return self.navigation.jump_forward_in_history()

    def jump_to_path(self, target: Path) -> None:
        self.navigation.jump_to_path(target)

    def jump_to_line(self, line_number: int) -> None:
        self.navigation.jump_to_line(line_number)

    # mouse callbacks
    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        if self.mouse is None:
            return False
        return self.mouse.handle_tree_mouse_click(mouse_key)

    def tick_source_selection_drag(self) -> None:
        if self.mouse is None:
            return
        self.mouse.tick_source_selection_drag()
