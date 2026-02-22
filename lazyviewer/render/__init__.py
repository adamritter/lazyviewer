"""Rendering engine for the split tree/text terminal view.

Defines render context data and writes fully composed ANSI frames.
Also computes status/help/search overlays without mutating runtime state.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ..ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line, slice_ansi_line
from ..symbols import SymbolEntry, collect_sticky_symbol_headers, next_symbol_start_line
from .help import (
    help_panel_lines,
    help_panel_row_count,
    render_help_page,
)
from ..tree import TreeEntry, clamp_left_width, format_tree_entry

FILTER_SPINNER_FRAMES: tuple[str, ...] = ("|", "/", "-", "\\")
SOURCE_SELECTION_BG_SGR = "48;2;58;92;188"


@dataclass
class RenderContext:
    text_lines: list[str]
    text_start: int
    tree_entries: list[TreeEntry]
    tree_start: int
    tree_selected: int
    max_lines: int
    current_path: Path
    tree_root: Path
    expanded: set[Path]
    width: int
    left_width: int
    text_x: int
    wrap_text: bool
    browser_visible: bool
    show_hidden: bool
    show_help: bool = False
    tree_filter_active: bool = False
    tree_filter_mode: str = "files"
    tree_filter_query: str = ""
    tree_filter_editing: bool = False
    tree_filter_cursor_visible: bool = False
    tree_filter_match_count: int = 0
    tree_filter_truncated: bool = False
    tree_filter_loading: bool = False
    tree_filter_spinner_frame: int = 0
    tree_filter_prefix: str = "p>"
    tree_filter_placeholder: str = "type to filter files"
    picker_active: bool = False
    picker_mode: str = "symbols"
    picker_query: str = ""
    picker_items: list[str] | None = None
    picker_selected: int = 0
    picker_focus: str = "query"
    picker_list_start: int = 0
    picker_message: str = ""
    git_status_overlay: dict[Path, int] | None = None
    tree_search_query: str = ""
    text_search_query: str = ""
    text_search_current_line: int = 0
    text_search_current_column: int = 0
    preview_is_git_diff: bool = False
    source_selection_anchor: tuple[int, int] | None = None
    source_selection_focus: tuple[int, int] | None = None


def render_dual_page_context(context: RenderContext) -> None:
    render_dual_page(
        context.text_lines,
        context.text_start,
        context.tree_entries,
        context.tree_start,
        context.tree_selected,
        context.max_lines,
        context.current_path,
        context.tree_root,
        context.expanded,
        context.width,
        context.left_width,
        context.text_x,
        context.wrap_text,
        context.browser_visible,
        context.show_hidden,
        show_help=context.show_help,
        tree_filter_active=context.tree_filter_active,
        tree_filter_mode=context.tree_filter_mode,
        tree_filter_query=context.tree_filter_query,
        tree_filter_editing=context.tree_filter_editing,
        tree_filter_cursor_visible=context.tree_filter_cursor_visible,
        tree_filter_match_count=context.tree_filter_match_count,
        tree_filter_truncated=context.tree_filter_truncated,
        tree_filter_loading=context.tree_filter_loading,
        tree_filter_spinner_frame=context.tree_filter_spinner_frame,
        tree_filter_prefix=context.tree_filter_prefix,
        tree_filter_placeholder=context.tree_filter_placeholder,
        picker_active=context.picker_active,
        picker_mode=context.picker_mode,
        picker_query=context.picker_query,
        picker_items=context.picker_items,
        picker_selected=context.picker_selected,
        picker_focus=context.picker_focus,
        picker_list_start=context.picker_list_start,
        picker_message=context.picker_message,
        git_status_overlay=context.git_status_overlay,
        tree_search_query=context.tree_search_query,
        text_search_query=context.text_search_query,
        text_search_current_line=context.text_search_current_line,
        text_search_current_column=context.text_search_current_column,
        preview_is_git_diff=context.preview_is_git_diff,
        source_selection_anchor=context.source_selection_anchor,
        source_selection_focus=context.source_selection_focus,
    )


def selected_with_ansi(text: str) -> str:
    """Apply selection styling without discarding existing ANSI colors."""
    if not text:
        return text

    # Keep reverse video active even when the text contains internal resets.
    return "\033[7m" + text.replace("\033[0m", "\033[0;7m") + "\033[0m"


def _help_line(lines: tuple[str, ...], row: int) -> str:
    if 0 <= row < len(lines):
        return lines[row]
    return ""


def _plain_display_width(text: str) -> int:
    return sum(char_display_width(ch, 0) for ch in text)


def _ansi_display_width(text: str) -> int:
    return _plain_display_width(ANSI_ESCAPE_RE.sub("", text))


def _underline_with_ansi(text: str) -> str:
    if not text:
        return text

    out: list[str] = ["\033[4m"]
    idx = 0
    while idx < len(text):
        if text[idx] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, idx)
            if match is not None:
                seq = match.group(0)
                if seq.endswith("m"):
                    params = seq[2:-1]
                    if params:
                        out.append(f"\033[{params};4m")
                    else:
                        out.append("\033[4m")
                else:
                    out.append(seq)
                idx = match.end()
                continue
        out.append(text[idx])
        idx += 1

    out.append("\033[24m")
    return "".join(out)


def build_status_line(left_text: str, width: int, right_text: str = "│ ? Help") -> str:
    usable = max(1, width - 1)
    if usable <= len(right_text):
        return right_text[-usable:]
    left_limit = max(0, usable - len(right_text) - 1)
    left = left_text[:left_limit]
    gap = " " * (usable - len(left) - len(right_text))
    return f"{left}{gap}{right_text}"


def _format_sticky_header_line(source_line: str, width: int) -> str:
    if width <= 0:
        return ""

    line_text = clip_ansi_line(source_line, width)
    underlined_line = _underline_with_ansi(line_text)
    line_width = _ansi_display_width(line_text)
    if line_width >= width:
        return underlined_line

    separator = "─" * (width - line_width)
    return f"{underlined_line}\033[2;38;5;245m{separator}\033[0m"


def _source_line_display_index(text_lines: list[str], source_line: int, wrap_text: bool) -> int | None:
    if not text_lines:
        return None

    target = max(1, source_line)
    if not wrap_text:
        idx = target - 1
        if 0 <= idx < len(text_lines):
            return idx
        return None

    current_source = 1
    for idx, line in enumerate(text_lines):
        if current_source >= target:
            return idx
        if _line_has_newline_terminator(line):
            current_source += 1
    return None


def _source_line_raw_text(
    text_lines: list[str],
    source_line: int,
    wrap_text: bool,
) -> str:
    start_idx = _source_line_display_index(text_lines, source_line, wrap_text)
    if start_idx is None:
        return ""

    if not wrap_text:
        return text_lines[start_idx].rstrip("\r\n")

    parts: list[str] = []
    for idx in range(start_idx, len(text_lines)):
        part = text_lines[idx]
        if _line_has_newline_terminator(part):
            parts.append(part.rstrip("\r\n"))
            break
        parts.append(part)
    return "".join(parts)


def _source_line_is_blank(
    text_lines: list[str],
    source_line: int,
    wrap_text: bool,
) -> bool:
    source_text = _source_line_raw_text(text_lines, source_line, wrap_text)
    plain_text = ANSI_ESCAPE_RE.sub("", source_text)
    return plain_text.strip() == ""


def _source_line_count(text_lines: list[str], wrap_text: bool) -> int:
    if not text_lines:
        return 0

    if not wrap_text:
        return len(text_lines)

    source_line = 1
    for line in text_lines:
        if _line_has_newline_terminator(line):
            source_line += 1
    if _line_has_newline_terminator(text_lines[-1]):
        return max(1, source_line - 1)
    return max(1, source_line)


def _next_nonblank_source_line(
    text_lines: list[str],
    start_line: int,
    wrap_text: bool,
) -> int | None:
    total_lines = _source_line_count(text_lines, wrap_text)
    for source_line in range(max(1, start_line), total_lines + 1):
        if not _source_line_is_blank(text_lines, source_line, wrap_text):
            return source_line
    return None


def _leading_indent_columns(text: str) -> int:
    col = 0
    for ch in text:
        if ch == " ":
            col += 1
        elif ch == "	":
            col += 4
        else:
            break
    return col


def _blank_line_exits_symbol_scope(
    text_lines: list[str],
    source_line: int,
    wrap_text: bool,
    current_path: Path,
    sticky_symbol: SymbolEntry,
) -> bool:
    next_nonblank = _next_nonblank_source_line(text_lines, source_line + 1, wrap_text)
    if next_nonblank is None:
        return True

    next_symbol_line = next_symbol_start_line(current_path, sticky_symbol.line + 1)
    if next_symbol_line is not None and next_nonblank == next_symbol_line:
        return True

    next_text = ANSI_ESCAPE_RE.sub("", _source_line_raw_text(text_lines, next_nonblank, wrap_text)).lstrip()
    if next_text.startswith("}"):
        return False

    header_plain = ANSI_ESCAPE_RE.sub("", _source_line_raw_text(text_lines, sticky_symbol.line + 1, wrap_text))
    next_plain = ANSI_ESCAPE_RE.sub("", _source_line_raw_text(text_lines, next_nonblank, wrap_text))
    if _leading_indent_columns(next_plain) <= _leading_indent_columns(header_plain):
        return True

    return False


def _source_line_exits_symbol_scope(
    text_lines: list[str],
    source_line: int,
    wrap_text: bool,
    current_path: Path,
    sticky_symbol: SymbolEntry,
) -> bool:
    if source_line <= (sticky_symbol.line + 1):
        return False

    if _source_line_is_blank(text_lines, source_line, wrap_text):
        return _blank_line_exits_symbol_scope(
            text_lines,
            source_line,
            wrap_text,
            current_path,
            sticky_symbol,
        )

    header_plain = ANSI_ESCAPE_RE.sub("", _source_line_raw_text(text_lines, sticky_symbol.line + 1, wrap_text))
    header_indent = _leading_indent_columns(header_plain)

    for candidate_line in range(sticky_symbol.line + 2, source_line + 1):
        if _source_line_is_blank(text_lines, candidate_line, wrap_text):
            continue
        candidate_plain = ANSI_ESCAPE_RE.sub("", _source_line_raw_text(text_lines, candidate_line, wrap_text))
        candidate_text = candidate_plain.lstrip()
        if candidate_text.startswith("}"):
            continue
        if _leading_indent_columns(candidate_plain) <= header_indent:
            return True

    return False


def sticky_symbol_headers_for_position(
    *,
    text_lines: list[str],
    text_start: int,
    content_rows: int,
    current_path: Path,
    wrap_text: bool,
    preview_is_git_diff: bool,
) -> list[SymbolEntry]:
    if preview_is_git_diff or not current_path.is_file() or content_rows <= 1:
        return []

    start_source, _, _ = _status_line_range(text_lines, text_start, 1, wrap_text)
    sticky_symbols = collect_sticky_symbol_headers(
        current_path,
        start_source,
        max_headers=max(1, content_rows - 1),
    )
    if not sticky_symbols:
        return []

    visible_symbols: list[SymbolEntry] = []
    for symbol in sticky_symbols:
        if _source_line_exits_symbol_scope(
            text_lines,
            start_source,
            wrap_text,
            current_path,
            symbol,
        ):
            continue
        visible_symbols.append(symbol)

    return visible_symbols


def _extract_source_line_text(
    text_lines: list[str],
    source_line: int,
    width: int,
    wrap_text: bool,
    text_x: int,
) -> str:
    source_text = _source_line_raw_text(text_lines, source_line, wrap_text)
    if not source_text:
        return ""

    if not wrap_text:
        return slice_ansi_line(source_text, text_x, width)

    return clip_ansi_line(source_text, width)


def _sticky_source_lines(
    text_lines: list[str],
    sticky_symbols: list[SymbolEntry],
    width: int,
    wrap_text: bool,
    text_x: int,
) -> list[str]:
    out: list[str] = []
    for symbol in sticky_symbols:
        source_line = symbol.line + 1
        sticky_text = _extract_source_line_text(
            text_lines,
            source_line,
            width,
            wrap_text,
            text_x,
        )
        out.append(sticky_text)
    return out


def _scroll_percent(text_start: int, total_lines: int, visible_rows: int) -> float:
    if total_lines <= 0:
        return 0.0
    max_start = max(0, total_lines - max(1, visible_rows))
    if max_start <= 0:
        return 0.0
    clamped_start = max(0, min(text_start, max_start))
    return (clamped_start / max_start) * 100.0


def _line_has_newline_terminator(line: str) -> bool:
    return line.endswith("\n") or line.endswith("\r")


def _status_line_range(
    text_lines: list[str],
    text_start: int,
    content_rows: int,
    wrap_text: bool,
) -> tuple[int, int, int]:
    if not text_lines:
        return 1, 1, 1

    if not wrap_text:
        total = len(text_lines)
        clamped_start = max(0, min(text_start, total - 1))
        end = min(total, clamped_start + max(1, content_rows))
        return clamped_start + 1, end, total

    display_to_source: list[int] = []
    source_line = 1
    for line in text_lines:
        display_to_source.append(source_line)
        if _line_has_newline_terminator(line):
            source_line += 1

    if _line_has_newline_terminator(text_lines[-1]):
        total_source_lines = max(1, source_line - 1)
    else:
        total_source_lines = max(1, source_line)

    clamped_start = max(0, min(text_start, len(text_lines) - 1))
    clamped_end = max(clamped_start, min(len(text_lines) - 1, clamped_start + max(1, content_rows) - 1))
    start_source = display_to_source[clamped_start]
    end_source = display_to_source[clamped_end]
    return start_source, end_source, total_source_lines


def _highlight_ansi_substrings(
    text: str,
    query: str,
    current_column: int | None = None,
    has_current_target: bool = False,
) -> str:
    if not text or not query:
        return text

    visible_chars: list[str] = []
    visible_start: list[int] = []
    visible_end: list[int] = []

    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                i = match.end()
                continue
        visible_start.append(i)
        visible_chars.append(text[i])
        i += 1
        visible_end.append(i)

    if not visible_chars:
        return text

    visible_text = "".join(visible_chars)
    folded_text = visible_text.casefold()
    folded_query = query.casefold()
    if not folded_query:
        return text

    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        idx = folded_text.find(folded_query, cursor)
        if idx < 0:
            break
        end = idx + len(query)
        spans.append((idx, end))
        cursor = end

    if not spans:
        return text

    current_idx: int | None = None
    if has_current_target and current_column is not None and spans:
        target = max(0, current_column - 1)
        for idx, (start_vis, end_vis) in enumerate(spans):
            if start_vis <= target < end_vis:
                current_idx = idx
                break
        if current_idx is None:
            for idx, (start_vis, _end_vis) in enumerate(spans):
                if start_vis >= target:
                    current_idx = idx
                    break
        if current_idx is None:
            current_idx = len(spans) - 1

    primary_start = "\033[7;1m"
    primary_end = "\033[27;22m"
    secondary_start = "\033[1m"
    secondary_end = "\033[22m"

    out: list[str] = []
    raw_cursor = 0
    for span_idx, (start_vis, end_vis) in enumerate(spans):
        if start_vis >= len(visible_start) or end_vis <= 0:
            continue
        start_raw = visible_start[start_vis]
        end_raw = visible_end[min(len(visible_end) - 1, end_vis - 1)]
        out.append(text[raw_cursor:start_raw])
        if has_current_target:
            if current_idx is not None and span_idx == current_idx:
                out.append(primary_start)
            else:
                out.append(secondary_start)
        else:
            out.append(primary_start)
        out.append(text[start_raw:end_raw])
        if has_current_target:
            if current_idx is not None and span_idx == current_idx:
                out.append(primary_end)
            else:
                out.append(secondary_end)
        else:
            out.append(primary_end)
        raw_cursor = end_raw
    out.append(text[raw_cursor:])
    return "".join(out)


def _highlight_segment_with_background(text: str) -> str:
    if not text:
        return text
    out: list[str] = [f"\033[{SOURCE_SELECTION_BG_SGR}m"]
    idx = 0
    while idx < len(text):
        if text[idx] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, idx)
            if match is not None:
                seq = match.group(0)
                if seq.endswith("m"):
                    params = seq[2:-1]
                    if params:
                        out.append(f"\033[{params};{SOURCE_SELECTION_BG_SGR}m")
                    else:
                        out.append(f"\033[{SOURCE_SELECTION_BG_SGR}m")
                else:
                    out.append(seq)
                idx = match.end()
                continue
        out.append(text[idx])
        idx += 1
    out.append("\033[49m")
    return "".join(out)


def _highlight_ansi_column_range(text: str, start_col: int, end_col: int) -> str:
    if not text:
        return text
    if end_col <= start_col:
        return text

    visible_start: list[int] = []
    visible_end: list[int] = []

    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                i = match.end()
                continue
        visible_start.append(i)
        i += 1
        visible_end.append(i)

    if not visible_start:
        return text

    start_idx = max(0, start_col)
    end_idx = min(len(visible_start), end_col)
    if end_idx <= start_idx:
        return text

    raw_start = visible_start[start_idx]
    raw_end = visible_end[end_idx - 1]
    return (
        text[:raw_start]
        + _highlight_segment_with_background(text[raw_start:raw_end])
        + text[raw_end:]
    )


def _normalized_selection_range(
    anchor: tuple[int, int] | None,
    focus: tuple[int, int] | None,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    if anchor is None and focus is None:
        return None
    if anchor is None:
        anchor = focus
    if focus is None:
        focus = anchor
    assert anchor is not None and focus is not None
    if focus < anchor:
        anchor, focus = focus, anchor
    return anchor, focus


def _selection_span_for_rendered_line(
    line_idx: int,
    line_plain_length: int,
    selection_range: tuple[tuple[int, int], tuple[int, int]] | None,
    viewport_start_col: int,
    viewport_end_col: int,
) -> tuple[int, int] | None:
    if selection_range is None:
        return None

    (start_line, start_col), (end_line, end_col) = selection_range
    if line_idx < start_line or line_idx > end_line:
        return None

    if start_line == end_line:
        abs_start = max(0, min(start_col, line_plain_length))
        abs_end = max(abs_start, min(end_col, line_plain_length))
    elif line_idx == start_line:
        abs_start = max(0, min(start_col, line_plain_length))
        abs_end = line_plain_length
    elif line_idx == end_line:
        abs_start = 0
        abs_end = max(0, min(end_col, line_plain_length))
    else:
        abs_start = 0
        abs_end = line_plain_length

    if abs_end <= abs_start:
        if line_plain_length <= abs_start:
            return None
        abs_end = abs_start + 1

    visible_start = max(abs_start, viewport_start_col)
    visible_end = min(abs_end, viewport_end_col)
    if visible_end <= visible_start:
        return None

    return visible_start - viewport_start_col, visible_end - viewport_start_col


def _format_tree_filter_status(
    query: str,
    match_count: int,
    truncated: bool,
    loading: bool,
    spinner_frame: int,
) -> str:
    if not query:
        return ""

    parts: list[str] = []
    if loading:
        spinner = FILTER_SPINNER_FRAMES[spinner_frame % len(FILTER_SPINNER_FRAMES)]
        parts.append(f"{spinner} searching")

    if match_count <= 0:
        if not loading:
            parts.append("no results")
    else:
        noun = "match" if match_count == 1 else "matches"
        parts.append(f"{match_count:,} {noun}")

    if truncated:
        parts.append("truncated")

    return " · ".join(parts)


def render_dual_page(
    text_lines: list[str],
    text_start: int,
    tree_entries: list[TreeEntry],
    tree_start: int,
    tree_selected: int,
    max_lines: int,
    current_path: Path,
    tree_root: Path,
    expanded: set[Path],
    width: int,
    left_width: int,
    text_x: int,
    wrap_text: bool,
    browser_visible: bool,
    show_hidden: bool,
    show_help: bool = False,
    tree_filter_active: bool = False,
    tree_filter_mode: str = "files",
    tree_filter_query: str = "",
    tree_filter_editing: bool = False,
    tree_filter_cursor_visible: bool = False,
    tree_filter_match_count: int = 0,
    tree_filter_truncated: bool = False,
    tree_filter_loading: bool = False,
    tree_filter_spinner_frame: int = 0,
    tree_filter_prefix: str = "p>",
    tree_filter_placeholder: str = "type to filter files",
    picker_active: bool = False,
    picker_mode: str = "symbols",
    picker_query: str = "",
    picker_items: list[str] | None = None,
    picker_selected: int = 0,
    picker_focus: str = "query",
    picker_list_start: int = 0,
    picker_message: str = "",
    git_status_overlay: dict[Path, int] | None = None,
    tree_search_query: str = "",
    text_search_query: str = "",
    text_search_current_line: int = 0,
    text_search_current_column: int = 0,
    preview_is_git_diff: bool = False,
    source_selection_anchor: tuple[int, int] | None = None,
    source_selection_focus: tuple[int, int] | None = None,
) -> None:
    out: list[str] = []
    out.append("\033[H\033[J")
    tree_help_lines, text_help_lines, text_only_help_lines = help_panel_lines(
        tree_filter_active=tree_filter_active,
        tree_filter_mode=tree_filter_mode,
        tree_filter_editing=tree_filter_editing,
    )
    help_rows = help_panel_row_count(
        max_lines,
        show_help,
        browser_visible=browser_visible,
        tree_filter_active=tree_filter_active,
        tree_filter_mode=tree_filter_mode,
        tree_filter_editing=tree_filter_editing,
    )
    content_rows = max(1, max_lines - help_rows)
    has_current_text_hit = text_search_current_line > 0 and text_search_current_column > 0
    selection_range = _normalized_selection_range(source_selection_anchor, source_selection_focus)

    sticky_symbols = sticky_symbol_headers_for_position(
        text_lines=text_lines,
        text_start=text_start,
        content_rows=content_rows,
        current_path=current_path,
        wrap_text=wrap_text,
        preview_is_git_diff=preview_is_git_diff,
    )

    if not browser_visible:
        line_width = max(1, width - 1)
        sticky_headers = [
            _format_sticky_header_line(line, line_width)
            for line in _sticky_source_lines(text_lines, sticky_symbols, line_width, wrap_text, text_x)
        ]
        sticky_header_rows = len(sticky_headers)
        text_content_rows = max(1, content_rows - sticky_header_rows)
        text_percent = _scroll_percent(text_start, len(text_lines), text_content_rows)
        status_start, status_end, status_total = _status_line_range(
            text_lines,
            text_start,
            text_content_rows,
            wrap_text,
        )
        for row in range(content_rows):
            if row < sticky_header_rows:
                sticky_text = sticky_headers[row]
                out.append(sticky_text)
                if "\033" in sticky_text:
                    out.append("\033[0m")
                out.append("\r\n")
                continue

            text_idx = text_start + row
            if text_idx < len(text_lines):
                full_line = text_lines[text_idx].rstrip("\r\n")
                line_plain_length = len(ANSI_ESCAPE_RE.sub("", full_line))
                if wrap_text:
                    text_raw = clip_ansi_line(full_line, line_width)
                    viewport_start_col = 0
                    viewport_end_col = line_width
                else:
                    text_raw = slice_ansi_line(full_line, text_x, line_width)
                    viewport_start_col = text_x
                    viewport_end_col = text_x + line_width
                current_col = text_search_current_column if text_idx + 1 == text_search_current_line else None
                text_raw = _highlight_ansi_substrings(
                    text_raw,
                    text_search_query,
                    current_column=current_col,
                    has_current_target=has_current_text_hit,
                )
                selection_span = _selection_span_for_rendered_line(
                    text_idx,
                    line_plain_length,
                    selection_range,
                    viewport_start_col,
                    viewport_end_col,
                )
                if selection_span is not None:
                    text_raw = _highlight_ansi_column_range(
                        text_raw,
                        selection_span[0],
                        selection_span[1],
                    )
                out.append(text_raw)
                if "\033" in text_raw:
                    out.append("\033[0m")
            out.append("\r\n")
        for row in range(help_rows):
            help_text = clip_ansi_line(_help_line(text_only_help_lines, row), line_width)
            out.append(help_text)
            if "\033" in help_text:
                out.append("\033[0m")
            out.append("\r\n")
        left_status = f"{current_path} ({status_start}-{status_end}/{status_total} {text_percent:5.1f}%)"
        status = build_status_line(left_status, width)
        out.append("\033[7m")
        out.append(status)
        out.append("\033[0m")
        os.write(sys.stdout.fileno(), "".join(out).encode("utf-8", errors="replace"))
        return

    left_width = clamp_left_width(width, left_width)
    divider_width = 1
    right_width = max(1, width - left_width - divider_width - 1)
    sticky_headers = [
        _format_sticky_header_line(line, right_width)
        for line in _sticky_source_lines(text_lines, sticky_symbols, right_width, wrap_text, text_x)
    ]
    sticky_header_rows = len(sticky_headers)
    text_content_rows = max(1, content_rows - sticky_header_rows)

    text_percent = _scroll_percent(text_start, len(text_lines), text_content_rows)
    status_start, status_end, status_total = _status_line_range(
        text_lines,
        text_start,
        text_content_rows,
        wrap_text,
    )
    picker_overlay_active = picker_active and picker_mode in {"symbols", "commands"}
    tree_filter_row_visible = tree_filter_active and not picker_overlay_active
    tree_row_offset = 1 if tree_filter_row_visible else 0
    items = picker_items if picker_overlay_active else []
    if items:
        picker_selected = max(0, min(picker_selected, len(items) - 1))
    else:
        picker_selected = 0
    picker_rows = max(1, content_rows - 1)
    max_picker_start = max(0, len(items) - picker_rows)
    picker_list_start = max(0, min(picker_list_start, max_picker_start))

    for row in range(content_rows):
        if picker_overlay_active:
            if picker_mode == "commands":
                query_prefix = ": "
                placeholder = "type to filter commands"
            else:
                query_prefix = "s> "
                placeholder = "type to filter symbols"
            if row == 0:
                if picker_query:
                    query_text = f"\033[1;38;5;81m{query_prefix}{picker_query}\033[0m"
                else:
                    query_text = f"\033[2;38;5;250m{query_prefix}{placeholder}\033[0m"
                tree_text = clip_ansi_line(query_text, left_width)
                if picker_focus == "query":
                    tree_text = selected_with_ansi(tree_text)
            else:
                picker_idx = picker_list_start + row - 1
                if picker_idx < len(items):
                    tree_text = clip_ansi_line(f" {items[picker_idx]}", left_width)
                    if picker_idx == picker_selected:
                        if picker_focus == "tree":
                            tree_text = selected_with_ansi(tree_text)
                        else:
                            tree_text = f"\033[38;5;81m{tree_text}\033[0m"
                elif row == 1 and picker_message:
                    tree_text = clip_ansi_line(f"\033[2;38;5;250m{picker_message}\033[0m", left_width)
                else:
                    tree_text = ""
        elif tree_filter_row_visible and row == 0:
            status_label = _format_tree_filter_status(
                tree_filter_query,
                tree_filter_match_count,
                tree_filter_truncated,
                tree_filter_loading,
                tree_filter_spinner_frame,
            )
            if tree_filter_editing:
                base = f"{tree_filter_prefix} {tree_filter_query}" if tree_filter_query else f"{tree_filter_prefix} "
                cursor = "_" if tree_filter_cursor_visible else " "
                query_text = f"\033[1;38;5;81m{base}{cursor}\033[0m"
            elif tree_filter_query:
                query_text = f"\033[1;38;5;81m{tree_filter_prefix} {tree_filter_query}\033[0m"
            else:
                query_text = f"\033[2;38;5;250m{tree_filter_prefix} {tree_filter_placeholder}\033[0m"
            if status_label:
                query_text += f"\033[2;38;5;250m  {status_label}\033[0m"
            tree_text = clip_ansi_line(query_text, left_width)
            if tree_filter_editing:
                tree_text = selected_with_ansi(tree_text)
        else:
            tree_idx = tree_start + row - tree_row_offset
            if tree_idx < len(tree_entries):
                tree_text = format_tree_entry(
                    tree_entries[tree_idx],
                    tree_root,
                    expanded,
                    git_status_overlay=git_status_overlay,
                    search_query=tree_search_query,
                )
                tree_text = clip_ansi_line(tree_text, left_width)
                if tree_idx == tree_selected:
                    tree_text = selected_with_ansi(tree_text)
            else:
                tree_text = ""
        out.append(tree_text)
        tree_plain = ANSI_ESCAPE_RE.sub("", tree_text)
        tree_len = sum(char_display_width(ch, 0) for ch in tree_plain)
        if tree_len < left_width:
            out.append(" " * (left_width - tree_len))

        out.append("\033[2m│\033[0m")

        if row < sticky_header_rows:
            sticky_text = sticky_headers[row]
            out.append(sticky_text)
            if "\033" in sticky_text:
                out.append("\033[0m")
        else:
            text_idx = text_start + row
            if text_idx < len(text_lines):
                full_line = text_lines[text_idx].rstrip("\r\n")
                line_plain_length = len(ANSI_ESCAPE_RE.sub("", full_line))
                if wrap_text:
                    text_raw = clip_ansi_line(full_line, right_width)
                    viewport_start_col = 0
                    viewport_end_col = right_width
                else:
                    text_raw = slice_ansi_line(full_line, text_x, right_width)
                    viewport_start_col = text_x
                    viewport_end_col = text_x + right_width
                current_col = text_search_current_column if text_idx + 1 == text_search_current_line else None
                text_raw = _highlight_ansi_substrings(
                    text_raw,
                    text_search_query,
                    current_column=current_col,
                    has_current_target=has_current_text_hit,
                )
                selection_span = _selection_span_for_rendered_line(
                    text_idx,
                    line_plain_length,
                    selection_range,
                    viewport_start_col,
                    viewport_end_col,
                )
                if selection_span is not None:
                    text_raw = _highlight_ansi_column_range(
                        text_raw,
                        selection_span[0],
                        selection_span[1],
                    )
                out.append(text_raw)
                if "\033" in text_raw:
                    out.append("\033[0m")
            else:
                out.append("")
        out.append("\r\n")

    for row in range(help_rows):
        left_help = clip_ansi_line(_help_line(tree_help_lines, row), left_width)
        out.append(left_help)
        left_plain = ANSI_ESCAPE_RE.sub("", left_help)
        left_len = sum(char_display_width(ch, 0) for ch in left_plain)
        if left_len < left_width:
            out.append(" " * (left_width - left_len))
        out.append("\033[2m│\033[0m")

        right_help = clip_ansi_line(_help_line(text_help_lines, row), right_width)
        out.append(right_help)
        if "\033" in right_help:
            out.append("\033[0m")
        out.append("\r\n")

    left_status = f"{current_path} ({status_start}-{status_end}/{status_total} {text_percent:5.1f}%)"
    status = build_status_line(left_status, width)
    out.append("\033[7m")
    out.append(status)
    out.append("\033[0m")

    os.write(sys.stdout.fileno(), "".join(out).encode("utf-8", errors="replace"))
