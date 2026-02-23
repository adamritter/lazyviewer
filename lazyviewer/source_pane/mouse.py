"""Source-pane mouse event helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .events import handle_preview_click
from ..state import AppState

SOURCE_SELECTION_DRAG_SCROLL_SPEED_NUMERATOR = 2
SOURCE_SELECTION_DRAG_SCROLL_SPEED_DENOMINATOR = 1


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
class SourcePaneMouseCallbacks:
    visible_content_rows: Callable[[], int]
    source_pane_col_bounds: Callable[[], tuple[int, int]]
    source_selection_position: Callable[[int, int], tuple[int, int] | None]
    directory_preview_target_for_display_line: Callable[[int], Path | None]
    max_horizontal_text_offset: Callable[[], int]
    maybe_grow_directory_preview: Callable[[], bool]
    clear_source_selection: Callable[[], bool]
    copy_selected_source_range: Callable[[tuple[int, int], tuple[int, int]], bool]
    open_tree_filter: Callable[[str], None]
    apply_tree_filter_query: Callable[..., None]
    jump_to_path: Callable[[Path], None]


@dataclass(frozen=True)
class SourcePaneClickResult:
    handled: bool
    route_to_tree: bool = False


class SourcePaneMouseHandlers:
    def __init__(
        self,
        state: AppState,
        callbacks: SourcePaneMouseCallbacks,
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
        self._open_tree_filter = callbacks.open_tree_filter
        self._apply_tree_filter_query = callbacks.apply_tree_filter_query
        self._jump_to_path = callbacks.jump_to_path
        self._drag = SourceSelectionDragState()

    def reset_source_selection_drag_state(self) -> None:
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

    def handle_click(
        self,
        col: int,
        row: int,
        is_left_down: bool,
        is_left_up: bool,
    ) -> SourcePaneClickResult:
        state = self._state
        if self._drag.active and is_left_down:
            self._update_drag_pointer(col, row)
            self.tick_source_selection_drag()
            return SourcePaneClickResult(handled=True)

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
                return SourcePaneClickResult(handled=True)
            if state.source_selection_anchor is None:
                self.reset_source_selection_drag_state()
                return SourcePaneClickResult(handled=True)
            state.source_selection_focus = selection_pos
            same_selection_pos = state.source_selection_anchor == selection_pos
            if same_selection_pos:
                handled = handle_preview_click(
                    state,
                    selection_pos,
                    directory_preview_target_for_display_line=self._directory_preview_target_for_display_line,
                    clear_source_selection=self._clear_source_selection,
                    reset_source_selection_drag_state=self.reset_source_selection_drag_state,
                    jump_to_path=self._jump_to_path,
                    open_tree_filter=self._open_tree_filter,
                    apply_tree_filter_query=self._apply_tree_filter_query,
                )
                if handled:
                    return SourcePaneClickResult(handled=True)
            self._copy_selected_source_range(state.source_selection_anchor, selection_pos)
            self.reset_source_selection_drag_state()
            state.dirty = True
            return SourcePaneClickResult(handled=True)

        if is_left_up:
            if self._drag.active and state.source_selection_anchor is not None:
                self._drag.pointer = (col, row)
                self.tick_source_selection_drag()
                end_pos = state.source_selection_focus or state.source_selection_anchor
                self._copy_selected_source_range(state.source_selection_anchor, end_pos)
                state.source_selection_focus = end_pos
                state.dirty = True
            self.reset_source_selection_drag_state()
            return SourcePaneClickResult(handled=True)

        if self._drag.active:
            # Keep live selection while dragging, even if pointer briefly leaves source pane.
            return SourcePaneClickResult(handled=True)

        if self._clear_source_selection():
            state.dirty = True
        self.reset_source_selection_drag_state()
        return SourcePaneClickResult(handled=False, route_to_tree=True)
