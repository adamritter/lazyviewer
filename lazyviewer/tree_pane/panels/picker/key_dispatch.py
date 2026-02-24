"""Picker key dispatch helpers for :mod:`tree_pane.panels.picker`."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .controller import NavigationController


def _parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
    parts = mouse_key.split(":")
    if len(parts) < 3:
        return None, None
    try:
        return int(parts[1]), int(parts[2])
    except Exception:
        return None, None


def _move_picker_selection(controller: NavigationController, direction: int) -> None:
    if not controller.state.picker_match_labels:
        return
    previous = controller.state.picker_selected
    controller.state.picker_selected = max(
        0,
        min(len(controller.state.picker_match_labels) - 1, controller.state.picker_selected + direction),
    )
    if controller.state.picker_selected != previous:
        controller.state.dirty = True


def _handle_picker_mouse_wheel(controller: NavigationController, mouse_key: str) -> None:
    direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
    col, _row = _parse_mouse_col_row(mouse_key)
    if controller.state.browser_visible and col is not None and col <= controller.state.left_width:
        _move_picker_selection(controller, direction)
        return
    previous_start = controller.state.start
    controller.state.start += direction * 3
    controller.state.start = max(0, min(controller.state.start, controller.state.max_start))
    if controller.state.start != previous_start:
        controller.state.dirty = True


def _handle_picker_mouse_click(
    controller: NavigationController,
    mouse_key: str,
    visible_rows: int,
    double_click_seconds: float,
    *,
    focus_query_row: bool,
) -> bool:
    col, row = _parse_mouse_col_row(mouse_key)
    if not (
        controller.state.browser_visible
        and col is not None
        and row is not None
        and 1 <= row <= visible_rows
        and col <= controller.state.left_width
    ):
        return False
    if row == 1:
        if focus_query_row:
            controller.state.picker_focus = "query"
            controller.state.dirty = True
        return False
    clicked_idx = controller.state.picker_list_start + (row - 2)
    if not (0 <= clicked_idx < len(controller.state.picker_match_labels)):
        return False
    previous = controller.state.picker_selected
    controller.state.picker_selected = clicked_idx
    if controller.state.picker_selected != previous:
        controller.state.dirty = True
    now = time.monotonic()
    is_double = clicked_idx == controller.state.last_click_idx and (
        now - controller.state.last_click_time
    ) <= double_click_seconds
    controller.state.last_click_idx = clicked_idx
    controller.state.last_click_time = now
    if not is_double:
        return False
    should_quit = controller.activate_picker_selection()
    if should_quit:
        return True
    controller.state.dirty = True
    return False


def handle_picker_key(
    controller: NavigationController,
    key: str,
    double_click_seconds: float,
) -> tuple[bool, bool]:
    """Handle one key while picker is active."""
    key_lower = key.lower()

    if not controller.state.picker_active:
        return False, False

    if key == "ESC" or key == "\x03":
        controller.close_picker()
        return True, False

    if controller.state.picker_mode == "commands":
        if key == "UP" or key_lower == "k":
            _move_picker_selection(controller, -1)
            return True, False
        if key == "DOWN" or key_lower == "j":
            _move_picker_selection(controller, 1)
            return True, False
        if key == "BACKSPACE":
            if controller.state.picker_query:
                controller.state.picker_query = controller.state.picker_query[:-1]
                controller.refresh_command_picker_matches(reset_selection=True)
                controller.state.dirty = True
            return True, False
        if len(key) == 1 and key.isprintable():
            controller.state.picker_query += key
            controller.refresh_command_picker_matches(reset_selection=True)
            controller.state.dirty = True
            return True, False
        if key == "ENTER" or key_lower == "l":
            should_quit = controller.activate_picker_selection()
            if should_quit:
                return True, True
            controller.state.dirty = True
            return True, False
        if key == "TAB":
            return True, False
        if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
            _handle_picker_mouse_wheel(controller, key)
            return True, False
        if key.startswith("MOUSE_LEFT_DOWN:"):
            should_quit = _handle_picker_mouse_click(
                controller,
                key,
                controller.visible_content_rows(),
                double_click_seconds,
                focus_query_row=False,
            )
            if should_quit:
                return True, True
            return True, False
        return True, False

    if key == "TAB":
        controller.state.picker_focus = "tree" if controller.state.picker_focus == "query" else "query"
        controller.state.dirty = True
        return True, False

    if controller.state.picker_focus == "query":
        if key == "ENTER":
            controller.state.picker_focus = "tree"
            controller.state.dirty = True
            return True, False
        if key == "BACKSPACE":
            if controller.state.picker_query:
                controller.state.picker_query = controller.state.picker_query[:-1]
                controller.refresh_active_picker_matches(reset_selection=True)
                controller.state.dirty = True
            return True, False
        if len(key) == 1 and key.isprintable():
            controller.state.picker_query += key
            controller.refresh_active_picker_matches(reset_selection=True)
            controller.state.dirty = True
        return True, False

    if key == "ENTER" or key_lower == "l":
        should_quit = controller.activate_picker_selection()
        if should_quit:
            return True, True
        controller.state.dirty = True
        return True, False
    if key == "UP" or key_lower == "k":
        _move_picker_selection(controller, -1)
        return True, False
    if key == "DOWN" or key_lower == "j":
        _move_picker_selection(controller, 1)
        return True, False
    if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
        _handle_picker_mouse_wheel(controller, key)
        return True, False
    if key.startswith("MOUSE_LEFT_DOWN:"):
        should_quit = _handle_picker_mouse_click(
            controller,
            key,
            controller.visible_content_rows(),
            double_click_seconds,
            focus_query_row=True,
        )
        if should_quit:
            return True, True
        return True, False
    return True, False
