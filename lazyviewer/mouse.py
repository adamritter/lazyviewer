"""Source-pane and mouse interaction helpers for app runtime."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .ansi import ANSI_ESCAPE_RE, char_display_width
from .preview.events import directory_preview_target_for_display_line, handle_preview_click
from .state import AppState

SOURCE_SELECTION_DRAG_SCROLL_SPEED_NUMERATOR = 2
SOURCE_SELECTION_DRAG_SCROLL_SPEED_DENOMINATOR = 1


def _parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
    parts = mouse_key.split(":")
    if len(parts) < 3:
        return None, None
    try:
        return int(parts[1]), int(parts[2])
    except Exception:
        return None, None


def _rendered_line_display_width(line: str) -> int:
    plain = ANSI_ESCAPE_RE.sub("", line).rstrip("\r\n")
    col = 0
    for ch in plain:
        col += char_display_width(ch, col)
    return col


def _drag_scroll_step(overshoot: int, span: int) -> int:
    if overshoot < 1:
        overshoot = 1
    base_step = max(1, min(max(1, span // 2), overshoot))
    return max(
        1,
        (
            base_step * SOURCE_SELECTION_DRAG_SCROLL_SPEED_NUMERATOR
            + SOURCE_SELECTION_DRAG_SCROLL_SPEED_DENOMINATOR
            - 1
        )
        // SOURCE_SELECTION_DRAG_SCROLL_SPEED_DENOMINATOR,
    )


class SourcePaneOps:
    def __init__(
        self,
        state: AppState,
        visible_content_rows: Callable[[], int],
        get_terminal_size: Callable[[tuple[int, int]], os.terminal_size] = shutil.get_terminal_size,
    ) -> None:
        self.state = state
        self.visible_content_rows = visible_content_rows
        self._get_terminal_size = get_terminal_size

    def preview_pane_width(self) -> int:
        if self.state.browser_visible:
            return max(1, self.state.right_width)
        term = self._get_terminal_size((80, 24))
        return max(1, term.columns - 1)

    def max_horizontal_text_offset(self) -> int:
        if self.state.wrap_text or not self.state.lines:
            return 0
        viewport_width = self.preview_pane_width()
        max_width = 0
        for line in self.state.lines:
            max_width = max(max_width, _rendered_line_display_width(line))
        return max(0, max_width - viewport_width)

    def source_pane_col_bounds(self) -> tuple[int, int]:
        if self.state.browser_visible:
            min_col = self.state.left_width + 2
            pane_width = max(1, self.state.right_width)
        else:
            min_col = 1
            pane_width = self.preview_pane_width()
        max_col = min_col + pane_width - 1
        return min_col, max_col

    def source_selection_position(self, col: int, row: int) -> tuple[int, int] | None:
        visible_rows = self.visible_content_rows()
        if row < 1 or row > visible_rows:
            return None

        if self.state.browser_visible:
            right_start_col = self.state.left_width + 2
            if col < right_start_col:
                return None
            text_col = max(0, col - right_start_col + self.state.text_x)
        else:
            right_start_col = 1
            if col < right_start_col:
                return None
            text_col = max(0, col - right_start_col + self.state.text_x)

        if not self.state.lines:
            return None
        line_idx = max(0, min(self.state.start + row - 1, len(self.state.lines) - 1))
        return line_idx, text_col

    def directory_preview_target_for_display_line(self, display_idx: int) -> Path | None:
        return directory_preview_target_for_display_line(self.state, display_idx)


def _copy_selected_source_range(
    state: AppState,
    start_pos: tuple[int, int],
    end_pos: tuple[int, int],
    copy_text_to_clipboard: Callable[[str], bool],
) -> bool:
    if not state.lines:
        return False

    start_line, start_col = start_pos
    end_line, end_col = end_pos
    if (end_line, end_col) < (start_line, start_col):
        start_line, start_col, end_line, end_col = end_line, end_col, start_line, start_col

    start_line = max(0, min(start_line, len(state.lines) - 1))
    end_line = max(0, min(end_line, len(state.lines) - 1))

    selected_parts: list[str] = []
    for idx in range(start_line, end_line + 1):
        plain = ANSI_ESCAPE_RE.sub("", state.lines[idx]).rstrip("\r\n")
        if idx == start_line and idx == end_line:
            left = max(0, min(start_col, len(plain)))
            right = max(left, min(end_col, len(plain)))
            selected_parts.append(plain[left:right])
        elif idx == start_line:
            left = max(0, min(start_col, len(plain)))
            selected_parts.append(plain[left:])
        elif idx == end_line:
            right = max(0, min(end_col, len(plain)))
            selected_parts.append(plain[:right])
        else:
            selected_parts.append(plain)

    selected_text = "\n".join(selected_parts)
    if not selected_text:
        fallback = ANSI_ESCAPE_RE.sub("", state.lines[start_line]).rstrip("\r\n")
        selected_text = fallback
    if not selected_text:
        return False
    return copy_text_to_clipboard(selected_text)


def _handle_tree_mouse_wheel(
    state: AppState,
    move_tree_selection: Callable[[int], bool],
    maybe_grow_directory_preview: Callable[[], bool],
    max_horizontal_text_offset: Callable[[], int],
    mouse_key: str,
) -> bool:
    is_vertical = mouse_key.startswith("MOUSE_WHEEL_UP:") or mouse_key.startswith("MOUSE_WHEEL_DOWN:")
    is_horizontal = mouse_key.startswith("MOUSE_WHEEL_LEFT:") or mouse_key.startswith("MOUSE_WHEEL_RIGHT:")
    if not (is_vertical or is_horizontal):
        return False

    col, _row = _parse_mouse_col_row(mouse_key)
    in_tree_pane = state.browser_visible and col is not None and col <= state.left_width

    if is_horizontal:
        if in_tree_pane:
            return True
        prev_text_x = state.text_x
        if mouse_key.startswith("MOUSE_WHEEL_LEFT:"):
            state.text_x = max(0, state.text_x - 4)
        else:
            state.text_x = min(max_horizontal_text_offset(), state.text_x + 4)
        if state.text_x != prev_text_x:
            state.dirty = True
        return True

    direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
    if in_tree_pane:
        if move_tree_selection(direction):
            state.dirty = True
        return True

    prev_start = state.start
    state.start += direction * 3
    state.start = max(0, min(state.start, state.max_start))
    grew_preview = direction > 0 and maybe_grow_directory_preview()
    if state.start != prev_start or grew_preview:
        state.dirty = True
    return True


@dataclass
class SourceSelectionDragState:
    active: bool = False
    pointer: tuple[int, int] | None = None
    vertical_edge: str | None = None
    horizontal_edge: str | None = None

    def reset(self) -> None:
        self.active = False
        self.pointer = None
        self.vertical_edge = None
        self.horizontal_edge = None


@dataclass(frozen=True)
class TreeMouseCallbacks:
    visible_content_rows: Callable[[], int]
    source_pane_col_bounds: Callable[[], tuple[int, int]]
    source_selection_position: Callable[[int, int], tuple[int, int] | None]
    directory_preview_target_for_display_line: Callable[[int], Path | None]
    max_horizontal_text_offset: Callable[[], int]
    maybe_grow_directory_preview: Callable[[], bool]
    clear_source_selection: Callable[[], bool]
    copy_selected_source_range: Callable[[tuple[int, int], tuple[int, int]], bool]
    rebuild_tree_entries: Callable[..., None]
    mark_tree_watch_dirty: Callable[[], None]
    coerce_tree_filter_result_index: Callable[[int], int | None]
    preview_selected_entry: Callable[..., None]
    activate_tree_filter_selection: Callable[[], None]
    open_tree_filter: Callable[[str], None]
    apply_tree_filter_query: Callable[..., None]
    jump_to_path: Callable[[Path], None]
    copy_text_to_clipboard: Callable[[str], bool]
    monotonic: Callable[[], float] = time.monotonic


class TreeMouseHandlers:
    def __init__(
        self,
        state: AppState,
        callbacks: TreeMouseCallbacks,
        double_click_seconds: float,
    ) -> None:
        self._state = state
        self._visible_content_rows = callbacks.visible_content_rows
        self._source_pane_col_bounds = callbacks.source_pane_col_bounds
        self._source_selection_position = callbacks.source_selection_position
        self._directory_preview_target_for_display_line = callbacks.directory_preview_target_for_display_line
        self._max_horizontal_text_offset = callbacks.max_horizontal_text_offset
        self._maybe_grow_directory_preview = callbacks.maybe_grow_directory_preview
        self._clear_source_selection = callbacks.clear_source_selection
        self._copy_selected_source_range = callbacks.copy_selected_source_range
        self._rebuild_tree_entries = callbacks.rebuild_tree_entries
        self._mark_tree_watch_dirty = callbacks.mark_tree_watch_dirty
        self._coerce_tree_filter_result_index = callbacks.coerce_tree_filter_result_index
        self._preview_selected_entry = callbacks.preview_selected_entry
        self._activate_tree_filter_selection = callbacks.activate_tree_filter_selection
        self._open_tree_filter = callbacks.open_tree_filter
        self._apply_tree_filter_query = callbacks.apply_tree_filter_query
        self._jump_to_path = callbacks.jump_to_path
        self._copy_text_to_clipboard = callbacks.copy_text_to_clipboard
        self._monotonic = callbacks.monotonic
        self._double_click_seconds = double_click_seconds
        self._drag = SourceSelectionDragState()

    def _reset_source_selection_drag_state(self) -> None:
        self._drag.reset()

    def _update_drag_pointer(self, col: int, row: int) -> None:
        visible_rows = self._visible_content_rows()
        previous_row = self._drag.pointer[1] if self._drag.pointer is not None else row
        previous_col = self._drag.pointer[0] if self._drag.pointer is not None else col
        self._drag.pointer = (col, row)

        if row < 1:
            self._drag.vertical_edge = "top"
        elif row > visible_rows:
            self._drag.vertical_edge = "bottom"
        elif row == 1 and (previous_row > row or self._drag.vertical_edge == "top"):
            self._drag.vertical_edge = "top"
        elif row == visible_rows and (previous_row < row or self._drag.vertical_edge == "bottom"):
            self._drag.vertical_edge = "bottom"
        else:
            self._drag.vertical_edge = None

        min_source_col, max_source_col = self._source_pane_col_bounds()
        if col < min_source_col:
            self._drag.horizontal_edge = "left"
        elif col > max_source_col:
            self._drag.horizontal_edge = "right"
        elif col == min_source_col and (previous_col > col or self._drag.horizontal_edge == "left"):
            self._drag.horizontal_edge = "left"
        elif col == max_source_col and (previous_col < col or self._drag.horizontal_edge == "right"):
            self._drag.horizontal_edge = "right"
        else:
            self._drag.horizontal_edge = None

    def tick_source_selection_drag(self) -> None:
        state = self._state
        if not self._drag.active or state.source_selection_anchor is None:
            return
        if self._drag.pointer is None:
            return

        col, row = self._drag.pointer
        visible_rows = self._visible_content_rows()
        if visible_rows <= 0:
            return

        min_source_col, max_source_col = self._source_pane_col_bounds()
        target_col = max(min_source_col, min(col, max_source_col))
        changed = False

        top_edge_active = row < 1 or (row == 1 and self._drag.vertical_edge == "top")
        bottom_edge_active = row > visible_rows or (row == visible_rows and self._drag.vertical_edge == "bottom")
        left_edge_active = col < min_source_col or (col == min_source_col and self._drag.horizontal_edge == "left")
        right_edge_active = col > max_source_col or (
            col == max_source_col and self._drag.horizontal_edge == "right"
        )

        if top_edge_active:
            overshoot = 1 - row
            step = _drag_scroll_step(overshoot, visible_rows)
            previous_start = state.start
            state.start = max(0, state.start - step)
            changed = state.start != previous_start
            target_row = 1
        elif bottom_edge_active:
            overshoot = row - visible_rows
            step = _drag_scroll_step(overshoot, visible_rows)
            previous_start = state.start
            state.start = min(state.max_start, state.start + step)
            grew_preview = False
            if state.start == previous_start:
                grew_preview = self._maybe_grow_directory_preview()
                if grew_preview:
                    state.start = min(state.max_start, state.start + step)
            changed = state.start != previous_start or grew_preview
            target_row = visible_rows
        else:
            target_row = row

        if left_edge_active:
            overshoot = min_source_col - col
            step = _drag_scroll_step(overshoot, max_source_col - min_source_col + 1)
            previous_text_x = state.text_x
            state.text_x = max(0, state.text_x - step)
            if state.text_x != previous_text_x:
                changed = True
        elif right_edge_active:
            overshoot = col - max_source_col
            step = _drag_scroll_step(overshoot, max_source_col - min_source_col + 1)
            previous_text_x = state.text_x
            state.text_x = min(self._max_horizontal_text_offset(), state.text_x + step)
            if state.text_x != previous_text_x:
                changed = True

        target_pos = self._source_selection_position(target_col, target_row)
        if target_pos is not None and target_pos != state.source_selection_focus:
            state.source_selection_focus = target_pos
            changed = True

        if changed:
            state.dirty = True

    def _toggle_directory_entry(
        self,
        resolved: Path,
        content_mode_toggle: bool = False,
    ) -> None:
        state = self._state
        if content_mode_toggle and state.tree_filter_active and state.tree_filter_mode == "content":
            if resolved in state.tree_filter_collapsed_dirs:
                state.tree_filter_collapsed_dirs.remove(resolved)
                state.expanded.add(resolved)
            else:
                if resolved != state.tree_root:
                    state.tree_filter_collapsed_dirs.add(resolved)
                state.expanded.discard(resolved)
        else:
            state.expanded.symmetric_difference_update({resolved})
        self._rebuild_tree_entries(preferred_path=resolved)
        self._mark_tree_watch_dirty()
        state.dirty = True

    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        state = self._state
        is_left_down = mouse_key.startswith("MOUSE_LEFT_DOWN:")
        is_left_up = mouse_key.startswith("MOUSE_LEFT_UP:")
        if not (is_left_down or is_left_up):
            return False

        col, row = _parse_mouse_col_row(mouse_key)
        if col is None or row is None:
            return True

        if self._drag.active and is_left_down:
            self._update_drag_pointer(col, row)
            self.tick_source_selection_drag()
            return True

        selection_pos = self._source_selection_position(col, row)
        if selection_pos is not None:
            if is_left_down:
                if not self._drag.active:
                    state.source_selection_anchor = selection_pos
                state.source_selection_focus = selection_pos
                self._drag.active = True
                self._drag.pointer = (col, row)
                self._drag.vertical_edge = None
                self._drag.horizontal_edge = None
                state.dirty = True
                return True
            if state.source_selection_anchor is None:
                self._reset_source_selection_drag_state()
                return True
            state.source_selection_focus = selection_pos
            same_selection_pos = state.source_selection_anchor == selection_pos
            if same_selection_pos:
                handled = handle_preview_click(
                    state,
                    selection_pos,
                    directory_preview_target_for_display_line=self._directory_preview_target_for_display_line,
                    clear_source_selection=self._clear_source_selection,
                    reset_source_selection_drag_state=self._reset_source_selection_drag_state,
                    jump_to_path=self._jump_to_path,
                    open_tree_filter=self._open_tree_filter,
                    apply_tree_filter_query=self._apply_tree_filter_query,
                )
                if handled:
                    return True
            self._copy_selected_source_range(state.source_selection_anchor, selection_pos)
            self._reset_source_selection_drag_state()
            state.dirty = True
            return True

        if is_left_up:
            if self._drag.active and state.source_selection_anchor is not None:
                self._drag.pointer = (col, row)
                self.tick_source_selection_drag()
                end_pos = state.source_selection_focus or state.source_selection_anchor
                self._copy_selected_source_range(state.source_selection_anchor, end_pos)
                state.source_selection_focus = end_pos
                state.dirty = True
            self._reset_source_selection_drag_state()
            return True

        if self._drag.active:
            # Keep live selection while dragging, even if pointer briefly leaves source pane.
            return True

        if self._clear_source_selection():
            state.dirty = True
        self._reset_source_selection_drag_state()

        if not (state.browser_visible and 1 <= row <= self._visible_content_rows() and col <= state.left_width):
            return True

        query_row_visible = state.tree_filter_active
        if query_row_visible and row == 1:
            state.tree_filter_editing = True
            state.dirty = True
            return True

        raw_clicked_idx = state.tree_start + (row - 1 - (1 if query_row_visible else 0))
        if not (0 <= raw_clicked_idx < len(state.tree_entries)):
            return True

        raw_clicked_entry = state.tree_entries[raw_clicked_idx]
        raw_arrow_col = 1 + (raw_clicked_entry.depth * 2)
        if is_left_down and raw_clicked_entry.is_dir and raw_arrow_col <= col <= (raw_arrow_col + 1):
            resolved = raw_clicked_entry.path.resolve()
            self._toggle_directory_entry(resolved, content_mode_toggle=True)
            state.last_click_idx = -1
            state.last_click_time = 0.0
            return True

        clicked_idx = self._coerce_tree_filter_result_index(raw_clicked_idx)
        if clicked_idx is None:
            return True

        prev_selected = state.selected_idx
        state.selected_idx = clicked_idx
        self._preview_selected_entry()
        if state.selected_idx != prev_selected:
            state.dirty = True

        now = self._monotonic()
        is_double = clicked_idx == state.last_click_idx and (now - state.last_click_time) <= self._double_click_seconds
        state.last_click_idx = clicked_idx
        state.last_click_time = now
        if not is_double:
            return True

        if state.tree_filter_active and state.tree_filter_query:
            self._activate_tree_filter_selection()
            return True

        entry = state.tree_entries[state.selected_idx]
        if entry.is_dir:
            resolved = entry.path.resolve()
            self._toggle_directory_entry(resolved)
            return True

        self._copy_text_to_clipboard(entry.path.name)
        state.dirty = True
        return True
