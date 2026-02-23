"""Preview-pane line mapping helpers for wrapped and diff-rendered content."""

from __future__ import annotations

from pathlib import Path

from ..ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line, slice_ansi_line
from ..highlight import read_text
from ..symbols import SymbolEntry, collect_sticky_symbol_headers, next_symbol_start_line

DIFF_REMOVED_BG_SGR = "48;2;92;43;49"
SOURCE_SELECTION_BG_SGR = "48;2;58;92;188"


def plain_display_width(text: str) -> int:
    return sum(char_display_width(ch, 0) for ch in text)


def ansi_display_width(text: str) -> int:
    return plain_display_width(ANSI_ESCAPE_RE.sub("", text))


def underline_with_ansi(text: str) -> str:
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


def format_sticky_header_line(source_line: str, width: int) -> str:
    if width <= 0:
        return ""

    line_text = clip_ansi_line(source_line, width)
    underlined_line = underline_with_ansi(line_text)
    line_width = ansi_display_width(line_text)
    if line_width >= width:
        return underlined_line

    separator = "─" * (width - line_width)
    return f"{underlined_line}\033[2;38;5;245m{separator}\033[0m"


def line_has_newline_terminator(line: str) -> bool:
    return line.endswith("\n") or line.endswith("\r")


def iter_diff_logical_line_ranges(
    text_lines: list[str],
    wrap_text: bool,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    idx = 0
    while idx < len(text_lines):
        start_idx = idx
        if wrap_text:
            while idx < len(text_lines) - 1 and not line_has_newline_terminator(text_lines[idx]):
                idx += 1
        ranges.append((start_idx, idx))
        idx += 1
    return ranges


def diff_preview_uses_plain_markers(
    text_lines: list[str],
    wrap_text: bool,
) -> bool:
    logical_ranges = iter_diff_logical_line_ranges(text_lines, wrap_text)
    if len(logical_ranges) < 3:
        return False

    sample_ranges = logical_ranges[:32]
    marker_like = 0
    plus_minus_marker_like = 0
    for start_idx, _end_idx in sample_ranges:
        plain = ANSI_ESCAPE_RE.sub("", text_lines[start_idx])
        if len(plain) >= 2 and plain[:2] in {"  ", "+ ", "- "}:
            marker_like += 1
            if plain[0] in {"+", "-"}:
                plus_minus_marker_like += 1

    return (
        marker_like >= max(3, (len(sample_ranges) * 3) // 4)
        and plus_minus_marker_like > 0
    )


def diff_preview_logical_line_is_removed(
    first_chunk: str,
    use_plain_markers: bool,
) -> bool:
    if use_plain_markers:
        plain = ANSI_ESCAPE_RE.sub("", first_chunk)
        return len(plain) >= 2 and plain[:2] == "- "
    return (f"\033[{DIFF_REMOVED_BG_SGR}m" in first_chunk) or (f";{DIFF_REMOVED_BG_SGR}m" in first_chunk)


def diff_source_line_for_display_index(
    text_lines: list[str],
    display_index: int,
    wrap_text: bool,
) -> int:
    if not text_lines:
        return 1

    clamped = max(0, min(display_index, len(text_lines) - 1))
    source_line = 1
    use_plain_markers = diff_preview_uses_plain_markers(text_lines, wrap_text)
    for start_idx, end_idx in iter_diff_logical_line_ranges(text_lines, wrap_text):
        is_removed = diff_preview_logical_line_is_removed(
            text_lines[start_idx],
            use_plain_markers=use_plain_markers,
        )
        if start_idx <= clamped <= end_idx:
            return source_line
        if not is_removed:
            source_line += 1

    return source_line


def source_line_display_index(
    text_lines: list[str],
    source_line: int,
    wrap_text: bool,
    preview_is_git_diff: bool = False,
) -> int | None:
    if not text_lines:
        return None

    target = max(1, source_line)
    if preview_is_git_diff:
        source_idx = 1
        use_plain_markers = diff_preview_uses_plain_markers(text_lines, wrap_text)
        for start_idx, _end_idx in iter_diff_logical_line_ranges(text_lines, wrap_text):
            is_removed = diff_preview_logical_line_is_removed(
                text_lines[start_idx],
                use_plain_markers=use_plain_markers,
            )
            if is_removed:
                continue
            if source_idx >= target:
                return start_idx
            source_idx += 1
        return None

    if not wrap_text:
        idx = target - 1
        if 0 <= idx < len(text_lines):
            return idx
        return None

    current_source = 1
    for idx, line in enumerate(text_lines):
        if current_source >= target:
            return idx
        if line_has_newline_terminator(line):
            current_source += 1
    return None


def source_line_raw_text(
    text_lines: list[str],
    source_line: int,
    wrap_text: bool,
    preview_is_git_diff: bool = False,
) -> str:
    start_idx = source_line_display_index(
        text_lines,
        source_line,
        wrap_text,
        preview_is_git_diff=preview_is_git_diff,
    )
    if start_idx is None:
        return ""

    if not wrap_text:
        return text_lines[start_idx].rstrip("\r\n")

    parts: list[str] = []
    for idx in range(start_idx, len(text_lines)):
        part = text_lines[idx]
        if line_has_newline_terminator(part):
            parts.append(part.rstrip("\r\n"))
            break
        parts.append(part)
    return "".join(parts)


def source_line_is_blank(
    text_lines: list[str],
    source_line: int,
    wrap_text: bool,
    preview_is_git_diff: bool = False,
) -> bool:
    source_text = source_line_raw_text(
        text_lines,
        source_line,
        wrap_text,
        preview_is_git_diff=preview_is_git_diff,
    )
    plain_text = ANSI_ESCAPE_RE.sub("", source_text)
    return plain_text.strip() == ""


def source_line_count(
    text_lines: list[str],
    wrap_text: bool,
    preview_is_git_diff: bool = False,
) -> int:
    if not text_lines:
        return 0

    if preview_is_git_diff:
        use_plain_markers = diff_preview_uses_plain_markers(text_lines, wrap_text)
        source_count = 0
        for start_idx, _end_idx in iter_diff_logical_line_ranges(text_lines, wrap_text):
            if diff_preview_logical_line_is_removed(
                text_lines[start_idx],
                use_plain_markers=use_plain_markers,
            ):
                continue
            source_count += 1
        return source_count

    if not wrap_text:
        return len(text_lines)

    source_line = 1
    for line in text_lines:
        if line_has_newline_terminator(line):
            source_line += 1
    if line_has_newline_terminator(text_lines[-1]):
        return max(1, source_line - 1)
    return max(1, source_line)


def next_nonblank_source_line(
    text_lines: list[str],
    start_line: int,
    wrap_text: bool,
    preview_is_git_diff: bool = False,
) -> int | None:
    total_lines = source_line_count(
        text_lines,
        wrap_text,
        preview_is_git_diff=preview_is_git_diff,
    )
    for source_line in range(max(1, start_line), total_lines + 1):
        if not source_line_is_blank(
            text_lines,
            source_line,
            wrap_text,
            preview_is_git_diff=preview_is_git_diff,
        ):
            return source_line
    return None


def status_line_range(
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
        if line_has_newline_terminator(line):
            source_line += 1

    if line_has_newline_terminator(text_lines[-1]):
        total_source_lines = max(1, source_line - 1)
    else:
        total_source_lines = max(1, source_line)

    clamped_start = max(0, min(text_start, len(text_lines) - 1))
    clamped_end = max(clamped_start, min(len(text_lines) - 1, clamped_start + max(1, content_rows) - 1))
    start_source = display_to_source[clamped_start]
    end_source = display_to_source[clamped_end]
    return start_source, end_source, total_source_lines


def leading_indent_columns(text: str) -> int:
    col = 0
    for ch in text:
        if ch == " ":
            col += 1
        elif ch == "\t":
            col += 4
        else:
            break
    return col


def blank_line_exits_symbol_scope(
    text_lines: list[str],
    source_line: int,
    wrap_text: bool,
    current_path: Path,
    sticky_symbol: SymbolEntry,
    preview_is_git_diff: bool = False,
) -> bool:
    next_nonblank = next_nonblank_source_line(
        text_lines,
        source_line + 1,
        wrap_text,
        preview_is_git_diff=preview_is_git_diff,
    )
    if next_nonblank is None:
        return True

    next_symbol_line = next_symbol_start_line(current_path, sticky_symbol.line + 1)
    if next_symbol_line is not None and next_nonblank == next_symbol_line:
        return True

    next_text = ANSI_ESCAPE_RE.sub(
        "",
        source_line_raw_text(
            text_lines,
            next_nonblank,
            wrap_text,
            preview_is_git_diff=preview_is_git_diff,
        ),
    ).lstrip()
    if next_text.startswith("}"):
        return False

    header_plain = ANSI_ESCAPE_RE.sub(
        "",
        source_line_raw_text(
            text_lines,
            sticky_symbol.line + 1,
            wrap_text,
            preview_is_git_diff=preview_is_git_diff,
        ),
    )
    next_plain = ANSI_ESCAPE_RE.sub(
        "",
        source_line_raw_text(
            text_lines,
            next_nonblank,
            wrap_text,
            preview_is_git_diff=preview_is_git_diff,
        ),
    )
    if leading_indent_columns(next_plain) <= leading_indent_columns(header_plain):
        return True

    return False


def source_line_exits_symbol_scope(
    text_lines: list[str],
    source_line: int,
    wrap_text: bool,
    current_path: Path,
    sticky_symbol: SymbolEntry,
    preview_is_git_diff: bool = False,
) -> bool:
    if source_line <= (sticky_symbol.line + 1):
        return False

    if source_line_is_blank(
        text_lines,
        source_line,
        wrap_text,
        preview_is_git_diff=preview_is_git_diff,
    ):
        return blank_line_exits_symbol_scope(
            text_lines,
            source_line,
            wrap_text,
            current_path,
            sticky_symbol,
            preview_is_git_diff=preview_is_git_diff,
        )

    header_plain = ANSI_ESCAPE_RE.sub(
        "",
        source_line_raw_text(
            text_lines,
            sticky_symbol.line + 1,
            wrap_text,
            preview_is_git_diff=preview_is_git_diff,
        ),
    )
    header_indent = leading_indent_columns(header_plain)

    for candidate_line in range(sticky_symbol.line + 2, source_line + 1):
        if source_line_is_blank(
            text_lines,
            candidate_line,
            wrap_text,
            preview_is_git_diff=preview_is_git_diff,
        ):
            continue
        candidate_plain = ANSI_ESCAPE_RE.sub(
            "",
            source_line_raw_text(
                text_lines,
                candidate_line,
                wrap_text,
                preview_is_git_diff=preview_is_git_diff,
            ),
        )
        candidate_text = candidate_plain.lstrip()
        if candidate_text.startswith("}"):
            continue
        if leading_indent_columns(candidate_plain) <= header_indent:
            return True

    return False


def sticky_symbol_headers_for_position(
    text_lines: list[str],
    text_start: int,
    content_rows: int,
    current_path: Path,
    wrap_text: bool,
    preview_is_git_diff: bool,
) -> list[SymbolEntry]:
    if not current_path.is_file() or content_rows <= 1:
        return []

    scope_text_lines = text_lines
    scope_wrap_text = wrap_text
    scope_preview_is_git_diff = preview_is_git_diff
    if preview_is_git_diff:
        # Diff previews inject removed lines, so scope checks should run against
        # the real source file to avoid expensive remapping while scrolling.
        try:
            scope_text_lines = read_text(current_path).splitlines()
            scope_wrap_text = False
            scope_preview_is_git_diff = False
        except Exception:
            # Fall back to diff-rendered lines if source read fails.
            pass

    def visible_sticky_symbols(candidates: list[SymbolEntry], source_line: int) -> list[SymbolEntry]:
        visible: list[SymbolEntry] = []
        for symbol in candidates:
            if source_line_exits_symbol_scope(
                scope_text_lines,
                source_line,
                scope_wrap_text,
                current_path,
                symbol,
                preview_is_git_diff=scope_preview_is_git_diff,
            ):
                continue
            visible.append(symbol)
        return visible

    if preview_is_git_diff:
        start_source = diff_source_line_for_display_index(text_lines, text_start, wrap_text)
    else:
        start_source, _, _ = status_line_range(text_lines, text_start, 1, wrap_text)
    max_headers = max(1, content_rows - 1)
    sticky_symbols = collect_sticky_symbol_headers(
        current_path,
        start_source,
        max_headers=max_headers,
    )
    if not sticky_symbols:
        return []

    visible_symbols = visible_sticky_symbols(sticky_symbols, start_source)
    if not visible_symbols:
        return []

    # Smooth nested transitions: when an inner symbol starts exactly at the
    # viewport top and an outer sticky already exists, include the inner symbol
    # immediately so scrolling by one line keeps body progression consistent.
    if len(visible_symbols) < max_headers:
        next_symbols = collect_sticky_symbol_headers(
            current_path,
            start_source + 1,
            max_headers=max_headers,
        )
        if next_symbols:
            visible_next = visible_sticky_symbols(next_symbols, start_source)
            if (
                len(visible_next) == (len(visible_symbols) + 1)
                and visible_next[:-1] == visible_symbols
                and (visible_next[-1].line + 1) == start_source
            ):
                return visible_next

    return visible_symbols


def extract_source_line_text(
    text_lines: list[str],
    source_line: int,
    width: int,
    wrap_text: bool,
    text_x: int,
    preview_is_git_diff: bool = False,
) -> str:
    source_text = source_line_raw_text(
        text_lines,
        source_line,
        wrap_text,
        preview_is_git_diff=preview_is_git_diff,
    )
    if not source_text:
        return ""

    if not wrap_text:
        return slice_ansi_line(source_text, text_x, width)

    return clip_ansi_line(source_text, width)


def sticky_source_lines(
    text_lines: list[str],
    sticky_symbols: list[SymbolEntry],
    width: int,
    wrap_text: bool,
    text_x: int,
    preview_is_git_diff: bool = False,
) -> list[str]:
    out: list[str] = []
    for symbol in sticky_symbols:
        source_line = symbol.line + 1
        sticky_text = extract_source_line_text(
            text_lines,
            source_line,
            width,
            wrap_text,
            text_x,
            preview_is_git_diff=preview_is_git_diff,
        )
        out.append(sticky_text)
    return out


def scroll_percent(text_start: int, total_lines: int, visible_rows: int) -> float:
    if total_lines <= 0:
        return 0.0
    max_start = max(0, total_lines - max(1, visible_rows))
    if max_start <= 0:
        return 0.0
    clamped_start = max(0, min(text_start, max_start))
    return (clamped_start / max_start) * 100.0


def highlight_ansi_substrings(
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

    idx = 0
    text_len = len(text)
    while idx < text_len:
        if text[idx] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, idx)
            if match:
                idx = match.end()
                continue
        visible_start.append(idx)
        visible_chars.append(text[idx])
        idx += 1
        visible_end.append(idx)

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
        found = folded_text.find(folded_query, cursor)
        if found < 0:
            break
        end = found + len(query)
        spans.append((found, end))
        cursor = end

    if not spans:
        return text

    current_idx: int | None = None
    if has_current_target and current_column is not None and spans:
        target = max(0, current_column - 1)
        for span_idx, (start_vis, end_vis) in enumerate(spans):
            if start_vis <= target < end_vis:
                current_idx = span_idx
                break
        if current_idx is None:
            for span_idx, (start_vis, _end_vis) in enumerate(spans):
                if start_vis >= target:
                    current_idx = span_idx
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


def highlight_ansi_column_range(text: str, start_col: int, end_col: int) -> str:
    if not text:
        return text
    if end_col <= start_col:
        return text

    visible_start: list[int] = []
    visible_end: list[int] = []

    idx = 0
    text_len = len(text)
    while idx < text_len:
        if text[idx] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, idx)
            if match:
                idx = match.end()
                continue
        visible_start.append(idx)
        idx += 1
        visible_end.append(idx)

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


def normalized_selection_range(
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


def selection_span_for_rendered_line(
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


def formatted_sticky_headers(
    text_lines: list[str],
    sticky_symbols: list[SymbolEntry],
    width: int,
    wrap_text: bool,
    text_x: int,
    preview_is_git_diff: bool = False,
) -> list[str]:
    return [
        format_sticky_header_line(line, width)
        for line in sticky_source_lines(
            text_lines,
            sticky_symbols,
            width,
            wrap_text,
            text_x,
            preview_is_git_diff=preview_is_git_diff,
        )
    ]


def rendered_preview_row(
    text_lines: list[str],
    text_idx: int,
    width: int,
    wrap_text: bool,
    text_x: int,
    text_search_query: str,
    text_search_current_line: int,
    text_search_current_column: int,
    has_current_text_hit: bool,
    selection_range: tuple[tuple[int, int], tuple[int, int]] | None,
) -> str:
    if text_idx >= len(text_lines):
        return ""

    full_line = text_lines[text_idx].rstrip("\r\n")
    line_plain_length = len(ANSI_ESCAPE_RE.sub("", full_line))
    if wrap_text:
        text_raw = clip_ansi_line(full_line, width)
        viewport_start_col = 0
        viewport_end_col = width
    else:
        text_raw = slice_ansi_line(full_line, text_x, width)
        viewport_start_col = text_x
        viewport_end_col = text_x + width

    current_col = text_search_current_column if text_idx + 1 == text_search_current_line else None
    text_raw = highlight_ansi_substrings(
        text_raw,
        text_search_query,
        current_column=current_col,
        has_current_target=has_current_text_hit,
    )
    selection_span = selection_span_for_rendered_line(
        text_idx,
        line_plain_length,
        selection_range,
        viewport_start_col,
        viewport_end_col,
    )
    if selection_span is not None:
        text_raw = highlight_ansi_column_range(
            text_raw,
            selection_span[0],
            selection_span[1],
        )
    return text_raw
