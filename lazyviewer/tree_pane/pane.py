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
        self.visible_content_rows = visible_content_rows
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
        self.mouse = TreePaneMouseHandlers(
            state=state,
            visible_content_rows=visible_content_rows,
            rebuild_tree_entries=self.filter.rebuild_tree_entries,
            mark_tree_watch_dirty=mark_tree_watch_dirty,
            coerce_tree_filter_result_index=self.filter.coerce_tree_filter_result_index,
            preview_selected_entry=preview_selected_entry,
            activate_tree_filter_selection=self.filter.activate_tree_filter_selection,
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

    def toggle_tree_filter_mode(self, mode: str) -> None:
        """Open/switch/close tree filter UI based on current editing state."""
        if self.state.tree_filter_active:
            if self.state.tree_filter_mode == mode and self.state.tree_filter_editing:
                self.filter.close_tree_filter(clear_query=True)
            elif self.state.tree_filter_mode != mode:
                self.filter.open_tree_filter(mode)
            else:
                self.state.tree_filter_editing = True
                self.state.dirty = True
            return
        self.filter.open_tree_filter(mode)

    def handle_picker_key(self, key: str, double_click_seconds: float) -> tuple[bool, bool]:
        """Dispatch one key through picker controller when picker is active."""
        key_lower = key.lower()
        state = self.state

        if not state.picker_active:
            return False, False

        if key == "ESC" or key == "\x03":
            self.navigation.close_picker()
            return True, False

        if state.picker_mode == "commands":
            if key == "UP" or key_lower == "k":
                self._move_picker_selection(-1)
                return True, False
            if key == "DOWN" or key_lower == "j":
                self._move_picker_selection(1)
                return True, False
            if key == "BACKSPACE":
                if state.picker_query:
                    state.picker_query = state.picker_query[:-1]
                    self.navigation.refresh_command_picker_matches(reset_selection=True)
                    state.dirty = True
                return True, False
            if len(key) == 1 and key.isprintable():
                state.picker_query += key
                self.navigation.refresh_command_picker_matches(reset_selection=True)
                state.dirty = True
                return True, False
            if key == "ENTER" or key_lower == "l":
                should_quit = self.navigation.activate_picker_selection()
                if should_quit:
                    return True, True
                state.dirty = True
                return True, False
            if key == "TAB":
                return True, False
            if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
                self._handle_picker_mouse_wheel(key)
                return True, False
            if key.startswith("MOUSE_LEFT_DOWN:"):
                should_quit = self._handle_picker_mouse_click(
                    key,
                    self.visible_content_rows(),
                    double_click_seconds,
                    focus_query_row=False,
                )
                if should_quit:
                    return True, True
                return True, False
            return True, False

        if key == "TAB":
            state.picker_focus = "tree" if state.picker_focus == "query" else "query"
            state.dirty = True
            return True, False

        if state.picker_focus == "query":
            if key == "ENTER":
                state.picker_focus = "tree"
                state.dirty = True
                return True, False
            if key == "BACKSPACE":
                if state.picker_query:
                    state.picker_query = state.picker_query[:-1]
                    self.navigation.refresh_active_picker_matches(reset_selection=True)
                    state.dirty = True
                return True, False
            if len(key) == 1 and key.isprintable():
                state.picker_query += key
                self.navigation.refresh_active_picker_matches(reset_selection=True)
                state.dirty = True
            return True, False

        if key == "ENTER" or key_lower == "l":
            should_quit = self.navigation.activate_picker_selection()
            if should_quit:
                return True, True
            state.dirty = True
            return True, False
        if key == "UP" or key_lower == "k":
            self._move_picker_selection(-1)
            return True, False
        if key == "DOWN" or key_lower == "j":
            self._move_picker_selection(1)
            return True, False
        if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
            self._handle_picker_mouse_wheel(key)
            return True, False
        if key.startswith("MOUSE_LEFT_DOWN:"):
            should_quit = self._handle_picker_mouse_click(
                key,
                self.visible_content_rows(),
                double_click_seconds,
                focus_query_row=True,
            )
            if should_quit:
                return True, True
            return True, False
        return True, False

    def handle_tree_filter_key(
        self,
        key: str,
        *,
        handle_tree_mouse_wheel: Callable[[str], bool],
        handle_tree_mouse_click: Callable[[str], bool],
    ) -> bool:
        """Dispatch one key through tree-filter controller when filter is active."""
        state = self.state
        if not state.tree_filter_active:
            return False

        def apply_live_filter_query(query: str) -> None:
            content_mode = state.tree_filter_mode == "content"
            self.filter.apply_tree_filter_query(
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
                self.filter.close_tree_filter(
                    clear_query=True,
                    restore_origin=state.tree_filter_mode == "content",
                )
                return True
            if key == "ENTER":
                self.filter.activate_tree_filter_selection()
                return True
            if key == "TAB":
                state.tree_filter_editing = False
                state.dirty = True
                return True
            if key == "UP" or key == "CTRL_K":
                if self.filter.move_tree_selection(-1):
                    state.dirty = True
                return True
            if key == "DOWN" or key == "CTRL_J":
                if self.filter.move_tree_selection(1):
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
                self.navigation.toggle_help_panel()
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
            self.filter.activate_tree_filter_selection()
            return True
        if key == "ESC":
            self.filter.close_tree_filter(clear_query=True)
            return True
        if state.tree_filter_mode == "content":
            if key == "n":
                if self.filter.jump_to_next_content_hit(1):
                    state.dirty = True
                return True
            if key in {"N", "p"}:
                if self.filter.jump_to_next_content_hit(-1):
                    state.dirty = True
                return True
        return False

    def _move_picker_selection(self, direction: int) -> None:
        """Move picker selection by ``direction`` while clamping to list bounds."""
        state = self.state
        if not state.picker_match_labels:
            return
        previous = state.picker_selected
        state.picker_selected = max(0, min(len(state.picker_match_labels) - 1, state.picker_selected + direction))
        if state.picker_selected != previous:
            state.dirty = True

    def _handle_picker_mouse_wheel(self, mouse_key: str) -> None:
        """Handle wheel scrolling inside picker context."""
        state = self.state
        direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
        col, _row = self._parse_mouse_col_row(mouse_key)
        if state.browser_visible and col is not None and col <= state.left_width:
            self._move_picker_selection(direction)
            return
        previous_start = state.start
        state.start += direction * 3
        state.start = max(0, min(state.start, state.max_start))
        if state.start != previous_start:
            state.dirty = True

    def _handle_picker_mouse_click(
        self,
        mouse_key: str,
        visible_rows: int,
        double_click_seconds: float,
        *,
        focus_query_row: bool,
    ) -> bool:
        """Process picker click selection and optional double-click activation."""
        state = self.state
        col, row = self._parse_mouse_col_row(mouse_key)
        if not (
            state.browser_visible
            and col is not None
            and row is not None
            and 1 <= row <= visible_rows
            and col <= state.left_width
        ):
            return False
        if row == 1:
            if focus_query_row:
                state.picker_focus = "query"
                state.dirty = True
            return False
        clicked_idx = state.picker_list_start + (row - 2)
        if not (0 <= clicked_idx < len(state.picker_match_labels)):
            return False
        previous = state.picker_selected
        state.picker_selected = clicked_idx
        if state.picker_selected != previous:
            state.dirty = True
        now = time.monotonic()
        is_double = clicked_idx == state.last_click_idx and (now - state.last_click_time) <= double_click_seconds
        state.last_click_idx = clicked_idx
        state.last_click_time = now
        if not is_double:
            return False
        should_quit = self.navigation.activate_picker_selection()
        if should_quit:
            return True
        state.dirty = True
        return False
