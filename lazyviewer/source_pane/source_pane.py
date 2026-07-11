"""Source-pane input controller."""

from __future__ import annotations

from collections.abc import Callable
import os
import shutil
from pathlib import Path

from ..session import SessionState
from ..ports import ApplyTreeFilterQuery
from .interaction.geometry import SourcePaneGeometry
from .interaction.mouse import SourcePaneClickResult, SourcePaneMouseHandlers


class SourcePane:
    """Own source-pane geometry and mouse interaction only."""

    def __init__(
        self,
        *,
        state: SessionState,
        visible_content_rows: Callable[[], int],
        move_tree_selection: Callable[[int], bool],
        maybe_grow_directory_preview: Callable[[], bool],
        clear_source_selection: Callable[[], bool],
        copy_selected_source_range: Callable[[tuple[int, int], tuple[int, int]], bool],
        directory_preview_target_for_display_line: Callable[[int], Path | None],
        open_tree_filter: Callable[[str], None],
        apply_tree_filter_query: ApplyTreeFilterQuery,
        jump_to_path: Callable[[Path], None],
        get_terminal_size: Callable[[tuple[int, int]], os.terminal_size] = shutil.get_terminal_size,
    ) -> None:
        self.state = state
        self._move_tree_selection = move_tree_selection
        self._maybe_grow_directory_preview = maybe_grow_directory_preview
        self.geometry = SourcePaneGeometry(
            state,
            visible_content_rows,
            get_terminal_size=get_terminal_size,
        )
        self.mouse = SourcePaneMouseHandlers(
            state=state,
            visible_content_rows=visible_content_rows,
            source_pane_col_bounds=self.geometry.source_pane_col_bounds,
            source_selection_position=self.geometry.source_selection_position,
            directory_preview_target_for_display_line=directory_preview_target_for_display_line,
            max_horizontal_text_offset=self.geometry.max_horizontal_text_offset,
            maybe_grow_directory_preview=maybe_grow_directory_preview,
            clear_source_selection=clear_source_selection,
            copy_selected_source_range=copy_selected_source_range,
            open_tree_filter=open_tree_filter,
            apply_tree_filter_query=apply_tree_filter_query,
            jump_to_path=jump_to_path,
        )

    @staticmethod
    def _parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
        parts = mouse_key.split(":")
        if len(parts) < 3:
            return None, None
        try:
            return int(parts[1]), int(parts[2])
        except ValueError:
            return None, None

    def handle_tree_mouse_wheel(self, mouse_key: str) -> bool:
        is_vertical = mouse_key.startswith("MOUSE_WHEEL_UP:") or mouse_key.startswith("MOUSE_WHEEL_DOWN:")
        is_horizontal = mouse_key.startswith("MOUSE_WHEEL_LEFT:") or mouse_key.startswith("MOUSE_WHEEL_RIGHT:")
        if not (is_vertical or is_horizontal):
            return False

        col, _row = self._parse_mouse_col_row(mouse_key)
        in_tree_pane = self.state.layout.browser_visible and col is not None and col <= self.state.layout.left_width

        if is_horizontal:
            if in_tree_pane:
                return True
            previous = self.state.preview.horizontal_scroll
            if mouse_key.startswith("MOUSE_WHEEL_LEFT:"):
                self.state.preview.horizontal_scroll = max(0, self.state.preview.horizontal_scroll - 4)
            else:
                self.state.preview.horizontal_scroll = min(
                    self.geometry.max_horizontal_text_offset(),
                    self.state.preview.horizontal_scroll + 4,
                )
            if self.state.preview.horizontal_scroll != previous:
                self.state.interface.dirty = True
            return True

        direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
        if in_tree_pane:
            if self._move_tree_selection(direction):
                self.state.interface.dirty = True
            return True

        previous = self.state.preview.scroll
        self.state.preview.scroll = max(
            0,
            min(self.state.preview.scroll + direction * 3, self.state.preview.max_scroll),
        )
        grew = direction > 0 and self._maybe_grow_directory_preview()
        if self.state.preview.scroll != previous or grew:
            self.state.interface.dirty = True
        return True

    def handle_tree_mouse_click(self, mouse_key: str) -> SourcePaneClickResult:
        is_left_down = mouse_key.startswith("MOUSE_LEFT_DOWN:")
        is_left_up = mouse_key.startswith("MOUSE_LEFT_UP:")
        if not (is_left_down or is_left_up):
            return SourcePaneClickResult(handled=False)
        col, row = self._parse_mouse_col_row(mouse_key)
        if col is None or row is None:
            return SourcePaneClickResult(handled=True)
        return self.mouse.handle_click(
            col=col,
            row=row,
            is_left_down=is_left_down,
            is_left_up=is_left_up,
        )

    def tick_source_selection_drag(self) -> None:
        self.mouse.tick_source_selection_drag()


__all__ = ["SourcePane"]
