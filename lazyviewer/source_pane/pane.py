"""Source pane runtime façade used by the application layer."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
import os
import shutil

from ..input.mouse import _handle_tree_mouse_wheel
from ..runtime.state import AppState
from .interaction.ops import SourcePaneOps


class SourcePane:
    """App-owned source pane object for geometry and wheel handling."""

    def __init__(
        self,
        *,
        state: AppState,
        visible_content_rows: Callable[[], int],
        move_tree_selection: Callable[[int], bool],
        maybe_grow_directory_preview: Callable[[], bool],
        get_terminal_size: Callable[[tuple[int, int]], os.terminal_size] = shutil.get_terminal_size,
    ) -> None:
        self._ops = SourcePaneOps(
            state,
            visible_content_rows,
            get_terminal_size=get_terminal_size,
        )
        self._handle_tree_mouse_wheel = partial(
            _handle_tree_mouse_wheel,
            state,
            move_tree_selection,
            maybe_grow_directory_preview,
            self.max_horizontal_text_offset,
        )

    def visible_content_rows(self) -> int:
        return self._ops.visible_content_rows()

    def max_horizontal_text_offset(self) -> int:
        return self._ops.max_horizontal_text_offset()

    def source_pane_col_bounds(self) -> tuple[int, int]:
        return self._ops.source_pane_col_bounds()

    def source_selection_position(self, col: int, row: int) -> tuple[int, int] | None:
        return self._ops.source_selection_position(col, row)

    def handle_tree_mouse_wheel(self, mouse_key: str) -> bool:
        return self._handle_tree_mouse_wheel(mouse_key)
