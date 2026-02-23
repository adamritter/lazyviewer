"""Mouse interaction routing for source and tree panes."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..source_pane import SourcePaneMouseCallbacks, SourcePaneMouseHandlers
from ..state import AppState
from ..tree_pane.events import TreePaneMouseCallbacks, TreePaneMouseHandlers


def _parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
    parts = mouse_key.split(":")
    if len(parts) < 3:
        return None, None
    try:
        return int(parts[1]), int(parts[2])
    except Exception:
        return None, None


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
        self._source_pane_handlers = SourcePaneMouseHandlers(
            state,
            SourcePaneMouseCallbacks(
                visible_content_rows=callbacks.visible_content_rows,
                source_pane_col_bounds=callbacks.source_pane_col_bounds,
                source_selection_position=callbacks.source_selection_position,
                directory_preview_target_for_display_line=callbacks.directory_preview_target_for_display_line,
                max_horizontal_text_offset=callbacks.max_horizontal_text_offset,
                maybe_grow_directory_preview=callbacks.maybe_grow_directory_preview,
                clear_source_selection=callbacks.clear_source_selection,
                copy_selected_source_range=callbacks.copy_selected_source_range,
                open_tree_filter=callbacks.open_tree_filter,
                apply_tree_filter_query=callbacks.apply_tree_filter_query,
                jump_to_path=callbacks.jump_to_path,
            ),
        )
        self._tree_pane_handlers = TreePaneMouseHandlers(
            state,
            TreePaneMouseCallbacks(
                visible_content_rows=callbacks.visible_content_rows,
                rebuild_tree_entries=callbacks.rebuild_tree_entries,
                mark_tree_watch_dirty=callbacks.mark_tree_watch_dirty,
                coerce_tree_filter_result_index=callbacks.coerce_tree_filter_result_index,
                preview_selected_entry=callbacks.preview_selected_entry,
                activate_tree_filter_selection=callbacks.activate_tree_filter_selection,
                copy_text_to_clipboard=callbacks.copy_text_to_clipboard,
                monotonic=callbacks.monotonic,
            ),
            double_click_seconds=double_click_seconds,
        )

    def tick_source_selection_drag(self) -> None:
        self._source_pane_handlers.tick_source_selection_drag()

    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        is_left_down = mouse_key.startswith("MOUSE_LEFT_DOWN:")
        is_left_up = mouse_key.startswith("MOUSE_LEFT_UP:")
        if not (is_left_down or is_left_up):
            return False

        col, row = _parse_mouse_col_row(mouse_key)
        if col is None or row is None:
            return True

        source_result = self._source_pane_handlers.handle_click(
            col=col,
            row=row,
            is_left_down=is_left_down,
            is_left_up=is_left_up,
        )
        if source_result.handled:
            return True
        if source_result.route_to_tree:
            return self._tree_pane_handlers.handle_click(col, row, is_left_down=is_left_down)
        return True
