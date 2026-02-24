"""Object wrapper around source-pane row rendering.

``SourcePaneRenderer`` computes sticky headers and scroll/status metadata once
per frame, then provides row-level rendering methods used by the top-level page
renderer.
"""

from __future__ import annotations

from pathlib import Path

from . import rendering as preview_rendering


class SourcePaneRenderer:
    """Precompute source-pane rendering context for one frame."""

    def __init__(
        self,
        text_lines: list[str],
        text_start: int,
        content_rows: int,
        line_width: int,
        current_path: Path,
        wrap_text: bool,
        text_x: int,
        text_search_query: str,
        text_search_current_line: int,
        text_search_current_column: int,
        preview_is_git_diff: bool,
        source_selection_anchor: tuple[int, int] | None,
        source_selection_focus: tuple[int, int] | None,
    ) -> None:
        """Initialize renderer state and sticky-header metadata."""
        self.text_lines = text_lines
        self.text_start = text_start
        self.line_width = line_width
        self.wrap_text = wrap_text
        self.text_x = text_x
        self.text_search_query = text_search_query
        self.text_search_current_line = text_search_current_line
        self.text_search_current_column = text_search_current_column
        self.preview_is_git_diff = preview_is_git_diff

        self._has_current_text_hit = text_search_current_line > 0 and text_search_current_column > 0
        self._selection_range = preview_rendering.normalized_selection_range(
            source_selection_anchor,
            source_selection_focus,
        )
        sticky_symbols = preview_rendering.sticky_symbol_headers_for_position(
            text_lines=text_lines,
            text_start=text_start,
            content_rows=content_rows,
            current_path=current_path,
            wrap_text=wrap_text,
            preview_is_git_diff=preview_is_git_diff,
        )
        self.sticky_headers = preview_rendering.formatted_sticky_headers(
            text_lines,
            sticky_symbols,
            line_width,
            wrap_text,
            text_x,
            preview_is_git_diff=preview_is_git_diff,
        )
        self.sticky_header_rows = len(self.sticky_headers)
        self.text_content_rows = max(1, content_rows - self.sticky_header_rows)
        self.text_percent = preview_rendering.scroll_percent(text_start, len(text_lines), self.text_content_rows)
        self.status_start, self.status_end, self.status_total = preview_rendering.status_line_range(
            text_lines,
            text_start,
            self.text_content_rows,
            wrap_text,
        )

    def render_row(self, row: int) -> str:
        """Render one visible source-pane row, including sticky-header rows."""
        if row < self.sticky_header_rows:
            return self.sticky_headers[row]

        text_idx = self.text_start + row
        if text_idx >= len(self.text_lines):
            return ""
        return preview_rendering.rendered_preview_row(
            self.text_lines,
            text_idx,
            self.line_width,
            self.wrap_text,
            self.text_x,
            self.text_search_query,
            self.text_search_current_line,
            self.text_search_current_column,
            self._has_current_text_hit,
            self._selection_range,
            preview_is_git_diff=self.preview_is_git_diff,
        )
