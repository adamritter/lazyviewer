"""Preview-pane line mapping helpers for wrapped and diff-rendered content.

This module is the stable public API for preview rendering helpers.
Implementations are split across focused helper modules.
"""

from __future__ import annotations

from .diffmap import (
    DIFF_REMOVED_BG_SGR,
    diff_preview_logical_line_is_removed,
    diff_preview_uses_plain_markers,
    diff_source_line_for_display_index,
    iter_diff_logical_line_ranges,
)
from .highlighting import (
    SOURCE_SELECTION_BG_SGR,
    _highlight_segment_with_background,
    highlight_ansi_column_range,
    highlight_ansi_substrings,
    normalized_selection_range,
    rendered_preview_row,
    selection_span_for_rendered_line,
)
from .source import (
    extract_source_line_text,
    next_nonblank_source_line,
    source_line_count,
    source_line_display_index,
    source_line_is_blank,
    source_line_raw_text,
    status_line_range,
    sticky_source_lines,
)
from .sticky import (
    blank_line_exits_symbol_scope,
    formatted_sticky_headers,
    leading_indent_columns,
    source_line_exits_symbol_scope,
    sticky_symbol_headers_for_position,
)
from .text import (
    ansi_display_width,
    format_sticky_header_line,
    line_has_newline_terminator,
    plain_display_width,
    scroll_percent,
    underline_with_ansi,
)

__all__ = [
    "DIFF_REMOVED_BG_SGR",
    "SOURCE_SELECTION_BG_SGR",
    "plain_display_width",
    "ansi_display_width",
    "underline_with_ansi",
    "format_sticky_header_line",
    "line_has_newline_terminator",
    "iter_diff_logical_line_ranges",
    "diff_preview_uses_plain_markers",
    "diff_preview_logical_line_is_removed",
    "diff_source_line_for_display_index",
    "source_line_display_index",
    "source_line_raw_text",
    "source_line_is_blank",
    "source_line_count",
    "next_nonblank_source_line",
    "status_line_range",
    "leading_indent_columns",
    "blank_line_exits_symbol_scope",
    "source_line_exits_symbol_scope",
    "sticky_symbol_headers_for_position",
    "extract_source_line_text",
    "sticky_source_lines",
    "scroll_percent",
    "highlight_ansi_substrings",
    "_highlight_segment_with_background",
    "highlight_ansi_column_range",
    "normalized_selection_range",
    "selection_span_for_rendered_line",
    "formatted_sticky_headers",
    "rendered_preview_row",
]
