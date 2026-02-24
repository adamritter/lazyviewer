"""Source-pane geometry and text-selection primitives.

These helpers convert terminal coordinates into source positions and implement
selection-copy behavior shared by keyboard/mouse interaction paths.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable

from ...render.ansi import ANSI_ESCAPE_RE, char_display_width
from ...runtime.state import AppState


def _rendered_line_display_width(line: str) -> int:
    """Return display width of one rendered line after stripping ANSI/newline."""
    plain = ANSI_ESCAPE_RE.sub("", line).rstrip("\r\n")
    col = 0
    for ch in plain:
        col += char_display_width(ch, col)
    return col


class SourcePaneGeometry:
    """Stateful source-pane geometry helpers bound to ``AppState``."""

    def __init__(
        self,
        state: AppState,
        visible_content_rows: Callable[[], int],
        get_terminal_size: Callable[[tuple[int, int]], os.terminal_size] = shutil.get_terminal_size,
    ) -> None:
        """Bind pane operations to shared state and terminal-size provider."""
        self.state = state
        self.visible_content_rows = visible_content_rows
        self._get_terminal_size = get_terminal_size
        self._max_text_offset_cache_key: tuple[int, int] | None = None
        self._max_text_offset_cache_value = 0

    def preview_pane_width(self) -> int:
        """Return current preview pane width in columns."""
        if self.state.browser_visible:
            return max(1, self.state.right_width)
        term = self._get_terminal_size((80, 24))
        return max(1, term.columns)

    def max_horizontal_text_offset(self) -> int:
        """Return max valid horizontal scroll offset for current rendered lines."""
        if self.state.wrap_text or not self.state.lines:
            return 0
        viewport_width = self.preview_pane_width()
        cache_key = (id(self.state.lines), viewport_width)
        if self._max_text_offset_cache_key == cache_key:
            return self._max_text_offset_cache_value
        max_width = 0
        for line in self.state.lines:
            max_width = max(max_width, _rendered_line_display_width(line))
        max_offset = max(0, max_width - viewport_width)
        self._max_text_offset_cache_key = cache_key
        self._max_text_offset_cache_value = max_offset
        return max_offset

    def source_pane_col_bounds(self) -> tuple[int, int]:
        """Return inclusive terminal column bounds of source pane."""
        if self.state.browser_visible:
            min_col = self.state.left_width + 2
            pane_width = max(1, self.state.right_width)
        else:
            min_col = 1
            pane_width = self.preview_pane_width()
        max_col = min_col + pane_width - 1
        return min_col, max_col

    def source_selection_position(self, col: int, row: int) -> tuple[int, int] | None:
        """Map terminal ``(col,row)`` to ``(line_idx,text_col)`` in rendered content."""
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


def copy_selected_source_range(
    state: AppState,
    start_pos: tuple[int, int],
    end_pos: tuple[int, int],
    copy_text_to_clipboard: Callable[[str], bool],
) -> bool:
    """Copy selected source range to clipboard using plain-text coordinates."""
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
