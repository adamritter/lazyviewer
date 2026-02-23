"""Preview-pane line mapping helpers for wrapped and diff-rendered content."""

from __future__ import annotations

from pathlib import Path

from ..ansi import ANSI_ESCAPE_RE, clip_ansi_line, slice_ansi_line
from ..highlight import read_text
from ..symbols import SymbolEntry, collect_sticky_symbol_headers, next_symbol_start_line

DIFF_REMOVED_BG_SGR = "48;2;92;43;49"


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
