"""Picker-mode keyboard handling."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..runtime.state import AppState
from .key_common import parse_mouse_col_row


def _move_picker_selection(state: AppState, direction: int) -> None:
    """Move picker selection by ``direction`` while clamping to list bounds."""
    if not state.picker_match_labels:
        return
    prev_selected = state.picker_selected
    state.picker_selected = max(
        0,
        min(len(state.picker_match_labels) - 1, state.picker_selected + direction),
    )
    if state.picker_selected != prev_selected:
        state.dirty = True


def _handle_picker_mouse_wheel(state: AppState, key: str) -> None:
    """Handle wheel scrolling inside picker context.

    Wheel input over the tree pane moves picker selection. Otherwise it scrolls
    source content preview by small fixed increments.
    """
    direction = -1 if key.startswith("MOUSE_WHEEL_UP:") else 1
    col, _row = parse_mouse_col_row(key)
    if state.browser_visible and col is not None and col <= state.left_width:
        _move_picker_selection(state, direction)
        return
    prev_start = state.start
    state.start += direction * 3
    state.start = max(0, min(state.start, state.max_start))
    if state.start != prev_start:
        state.dirty = True


@dataclass(frozen=True)
class PickerKeyCallbacks:
    """External operations required for picker key handling."""

    close_picker: Callable[[], None]
    refresh_command_picker_matches: Callable[..., None]
    activate_picker_selection: Callable[[], bool]
    visible_content_rows: Callable[[], int]
    refresh_active_picker_matches: Callable[..., None]


def _handle_picker_mouse_click(
    state: AppState,
    key: str,
    visible_rows: int,
    double_click_seconds: float,
    activate_picker_selection: Callable[[], bool],
    focus_query_row: bool,
) -> bool:
    """Process picker click selection and optional double-click activation."""
    col, row = parse_mouse_col_row(key)
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
    prev_selected = state.picker_selected
    state.picker_selected = clicked_idx
    if state.picker_selected != prev_selected:
        state.dirty = True
    now = time.monotonic()
    is_double = clicked_idx == state.last_click_idx and (now - state.last_click_time) <= double_click_seconds
    state.last_click_idx = clicked_idx
    state.last_click_time = now
    if not is_double:
        return False
    should_quit = activate_picker_selection()
    if should_quit:
        return True
    state.dirty = True
    return False


def handle_picker_key(
    key: str,
    state: AppState,
    double_click_seconds: float,
    callbacks: PickerKeyCallbacks,
) -> tuple[bool, bool]:
    """Handle one key while picker is active.

    Returns ``(handled, should_quit)`` so the main loop can stop event
    propagation and optionally terminate the application.
    """
    close_picker = callbacks.close_picker
    refresh_command_picker_matches = callbacks.refresh_command_picker_matches
    activate_picker_selection = callbacks.activate_picker_selection
    visible_content_rows = callbacks.visible_content_rows
    refresh_active_picker_matches = callbacks.refresh_active_picker_matches
    key_lower = key.lower()

    if not state.picker_active:
        return False, False

    if key == "ESC" or key == "\x03":
        close_picker()
        return True, False

    if state.picker_mode == "commands":
        if key == "UP" or key_lower == "k":
            _move_picker_selection(state, -1)
            return True, False
        if key == "DOWN" or key_lower == "j":
            _move_picker_selection(state, 1)
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
        if key == "ENTER" or key_lower == "l":
            should_quit = activate_picker_selection()
            if should_quit:
                return True, True
            state.dirty = True
            return True, False
        if key == "TAB":
            return True, False
        if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
            _handle_picker_mouse_wheel(state, key)
            return True, False
        if key.startswith("MOUSE_LEFT_DOWN:"):
            should_quit = _handle_picker_mouse_click(
                state,
                key,
                visible_content_rows(),
                double_click_seconds,
                activate_picker_selection,
                False,
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
                refresh_active_picker_matches(reset_selection=True)
                state.dirty = True
            return True, False
        if len(key) == 1 and key.isprintable():
            state.picker_query += key
            refresh_active_picker_matches(reset_selection=True)
            state.dirty = True
        return True, False

    if key == "ENTER" or key_lower == "l":
        should_quit = activate_picker_selection()
        if should_quit:
            return True, True
        state.dirty = True
        return True, False
    if key == "UP" or key_lower == "k":
        _move_picker_selection(state, -1)
        return True, False
    if key == "DOWN" or key_lower == "j":
        _move_picker_selection(state, 1)
        return True, False
    if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
        _handle_picker_mouse_wheel(state, key)
        return True, False
    if key.startswith("MOUSE_LEFT_DOWN:"):
        should_quit = _handle_picker_mouse_click(
            state,
            key,
            visible_content_rows(),
            double_click_seconds,
            activate_picker_selection,
            True,
        )
        if should_quit:
            return True, True
        return True, False
    return True, False
