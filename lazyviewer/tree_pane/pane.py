"""Tree pane runtime façade used by the application layer."""

from __future__ import annotations

from collections.abc import Callable
import time

from ..runtime.state import AppState
from .events import TreePaneMouseHandlers
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
        copy_text_to_clipboard: Callable[[str], bool],
        double_click_seconds: float,
        monotonic: Callable[[], float] = time.monotonic,
        on_tree_filter_state_change: Callable[[], None] | None = None,
    ) -> None:
        self.state = state
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
        self.filter_panel = self.filter.panel
        self.picker_panel = self.navigation.panel
        self.mouse = TreePaneMouseHandlers(
            state=state,
            visible_content_rows=visible_content_rows,
            rebuild_tree_entries=self.filter.rebuild_tree_entries,
            mark_tree_watch_dirty=mark_tree_watch_dirty,
            coerce_tree_filter_result_index=self.filter.coerce_tree_filter_result_index,
            preview_selected_entry=preview_selected_entry,
            activate_tree_filter_selection=self.filter_panel.activate_selection,
            copy_text_to_clipboard=copy_text_to_clipboard,
            double_click_seconds=double_click_seconds,
            monotonic=monotonic,
        )

    @staticmethod
    def _parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
        parts = mouse_key.split(":")
        if len(parts) < 3:
            return None, None
        try:
            return int(parts[1]), int(parts[2])
        except Exception:
            return None, None

    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        is_left_down = mouse_key.startswith("MOUSE_LEFT_DOWN:")
        is_left_up = mouse_key.startswith("MOUSE_LEFT_UP:")
        if not (is_left_down or is_left_up):
            return False
        col, row = self._parse_mouse_col_row(mouse_key)
        if col is None or row is None:
            return True
        return self.mouse.handle_click(col, row, is_left_down=is_left_down)
