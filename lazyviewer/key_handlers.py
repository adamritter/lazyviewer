from __future__ import annotations

import time
from collections.abc import Callable

from .state import AppState


def handle_picker_key(
    *,
    key: str,
    state: AppState,
    double_click_seconds: float,
    close_picker: Callable[[], None],
    refresh_command_picker_matches: Callable[..., None],
    activate_picker_selection: Callable[[], bool],
    visible_content_rows: Callable[[], int],
    refresh_active_picker_matches: Callable[..., None],
) -> tuple[bool, bool]:
    if not state.picker_active:
        return False, False

    if key == "ESC" or key == "\x03":
        close_picker()
        return True, False

    if state.picker_mode == "commands":
        if key == "UP" or key.lower() == "k":
            if state.picker_match_labels:
                state.picker_selected = max(0, state.picker_selected - 1)
                state.dirty = True
            return True, False
        if key == "DOWN" or key.lower() == "j":
            if state.picker_match_labels:
                state.picker_selected = min(len(state.picker_match_labels) - 1, state.picker_selected + 1)
                state.dirty = True
            return True, False
        if key == "BACKSPACE":
            if state.picker_query:
                state.picker_query = state.picker_query[:-1]
                refresh_command_picker_matches(reset_selection=True)
                state.dirty = True
            return True, False
        if len(key) == 1 and key.isprintable():
            state.picker_query += key
            refresh_command_picker_matches(reset_selection=True)
            state.dirty = True
            return True, False
        if key == "ENTER" or key.lower() == "l":
            should_quit = activate_picker_selection()
            if should_quit:
                return True, True
            state.dirty = True
            return True, False
        if key == "TAB":
            return True, False
        if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
            direction = -1 if key.startswith("MOUSE_WHEEL_UP:") else 1
            parts = key.split(":")
            col: int | None = None
            if len(parts) >= 3:
                try:
                    col = int(parts[1])
                except Exception:
                    col = None
            if state.browser_visible and col is not None and col <= state.left_width:
                if state.picker_match_labels:
                    prev_selected = state.picker_selected
                    state.picker_selected = max(
                        0,
                        min(len(state.picker_match_labels) - 1, state.picker_selected + direction),
                    )
                    if state.picker_selected != prev_selected:
                        state.dirty = True
            else:
                prev_start = state.start
                state.start += direction * 3
                state.start = max(0, min(state.start, state.max_start))
                if state.start != prev_start:
                    state.dirty = True
            return True, False
        if key.startswith("MOUSE_LEFT_DOWN:"):
            parts = key.split(":")
            if len(parts) >= 3:
                try:
                    col = int(parts[1])
                    row = int(parts[2])
                except Exception:
                    col = None
                    row = None
                if (
                    state.browser_visible
                    and col is not None
                    and row is not None
                    and 1 <= row <= visible_content_rows()
                    and col <= state.left_width
                ):
                    if row > 1:
                        clicked_idx = state.picker_list_start + (row - 2)
                        if 0 <= clicked_idx < len(state.picker_match_labels):
                            prev_selected = state.picker_selected
                            state.picker_selected = clicked_idx
                            if state.picker_selected != prev_selected:
                                state.dirty = True
                            now = time.monotonic()
                            is_double = (
                                clicked_idx == state.last_click_idx
                                and (now - state.last_click_time) <= double_click_seconds
                            )
                            state.last_click_idx = clicked_idx
                            state.last_click_time = now
                            if is_double:
                                should_quit = activate_picker_selection()
                                if should_quit:
                                    return True, True
                                state.dirty = True
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
                refresh_active_picker_matches(reset_selection=True)
                state.dirty = True
            return True, False
        if len(key) == 1 and key.isprintable():
            state.picker_query += key
            refresh_active_picker_matches(reset_selection=True)
            state.dirty = True
        return True, False

    if key == "ENTER" or key.lower() == "l":
        should_quit = activate_picker_selection()
        if should_quit:
            return True, True
        state.dirty = True
        return True, False
    if key == "UP" or key.lower() == "k":
        if state.picker_match_labels:
            state.picker_selected = max(0, state.picker_selected - 1)
            state.dirty = True
        return True, False
    if key == "DOWN" or key.lower() == "j":
        if state.picker_match_labels:
            state.picker_selected = min(len(state.picker_match_labels) - 1, state.picker_selected + 1)
            state.dirty = True
        return True, False
    if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
        direction = -1 if key.startswith("MOUSE_WHEEL_UP:") else 1
        parts = key.split(":")
        col: int | None = None
        if len(parts) >= 3:
            try:
                col = int(parts[1])
            except Exception:
                col = None
        if state.browser_visible and col is not None and col <= state.left_width:
            if state.picker_match_labels:
                prev_selected = state.picker_selected
                state.picker_selected = max(
                    0,
                    min(len(state.picker_match_labels) - 1, state.picker_selected + direction),
                )
                if state.picker_selected != prev_selected:
                    state.dirty = True
        else:
            prev_start = state.start
            state.start += direction * 3
            state.start = max(0, min(state.start, state.max_start))
            if state.start != prev_start:
                state.dirty = True
        return True, False
    if key.startswith("MOUSE_LEFT_DOWN:"):
        parts = key.split(":")
        if len(parts) >= 3:
            try:
                col = int(parts[1])
                row = int(parts[2])
            except Exception:
                col = None
                row = None
            if (
                state.browser_visible
                and col is not None
                and row is not None
                and 1 <= row <= visible_content_rows()
                and col <= state.left_width
            ):
                if row == 1:
                    state.picker_focus = "query"
                    state.dirty = True
                else:
                    clicked_idx = state.picker_list_start + (row - 2)
                    if 0 <= clicked_idx < len(state.picker_match_labels):
                        prev_selected = state.picker_selected
                        state.picker_selected = clicked_idx
                        if state.picker_selected != prev_selected:
                            state.dirty = True
                        now = time.monotonic()
                        is_double = (
                            clicked_idx == state.last_click_idx
                            and (now - state.last_click_time) <= double_click_seconds
                        )
                        state.last_click_idx = clicked_idx
                        state.last_click_time = now
                        if is_double:
                            should_quit = activate_picker_selection()
                            if should_quit:
                                return True, True
                            state.dirty = True
        return True, False
    return True, False


def handle_tree_filter_key(
    *,
    key: str,
    state: AppState,
    handle_tree_mouse_wheel: Callable[[str], bool],
    handle_tree_mouse_click: Callable[[str], bool],
    close_tree_filter: Callable[..., None],
    activate_tree_filter_selection: Callable[[], None],
    move_tree_selection: Callable[[int], bool],
    apply_tree_filter_query: Callable[..., None],
    jump_to_next_content_hit: Callable[[int], bool],
) -> bool:
    if state.tree_filter_active and state.tree_filter_editing:
        if handle_tree_mouse_wheel(key):
            return True
        if handle_tree_mouse_click(key):
            return True
        if key == "ESC":
            close_tree_filter(clear_query=True)
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
                apply_tree_filter_query(
                    state.tree_filter_query[:-1],
                    preview_selection=True,
                    select_first_file=True,
                )
            return True
        if key == "CTRL_U":
            if state.tree_filter_query:
                apply_tree_filter_query(
                    "",
                    preview_selection=True,
                    select_first_file=True,
                )
            return True
        if len(key) == 1 and key.isprintable():
            apply_tree_filter_query(
                state.tree_filter_query + key,
                preview_selection=True,
                select_first_file=True,
            )
            return True
        return True

    if state.tree_filter_active and not state.tree_filter_editing:
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
            if key == "N":
                if jump_to_next_content_hit(-1):
                    state.dirty = True
                return True
    return False
