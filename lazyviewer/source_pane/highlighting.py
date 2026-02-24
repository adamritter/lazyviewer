"""Apply search-hit and selection highlighting to ANSI-rendered source rows.

Functions here preserve existing ANSI style sequences while layering additional
emphasis for query matches and source-selection ranges.
"""

from __future__ import annotations

from ..render.ansi import ANSI_ESCAPE_RE, clip_ansi_line, slice_ansi_line

SOURCE_SELECTION_BG_SGR = "48;2;58;92;188"


def highlight_ansi_substrings(
    text: str,
    query: str,
    current_column: int | None = None,
    has_current_target: bool = False,
) -> str:
    """Highlight case-insensitive query matches in ANSI text.

    When ``has_current_target`` is true, the current hit gets stronger emphasis
    while other hits are still highlighted.
    """
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
    """Apply selection background while preserving existing ANSI attributes."""
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
    """Highlight an ANSI text segment spanning display columns ``[start_col,end_col)``."""
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
    """Normalize nullable selection endpoints into ordered ``(anchor, focus)`` pair."""
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
    """Project absolute selection range onto one rendered line's viewport columns."""
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
    """Render one source row with viewport clipping, search, and selection overlays."""
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
