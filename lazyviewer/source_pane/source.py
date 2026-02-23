"""Source-line mapping and extraction helpers for preview rendering."""

from __future__ import annotations

from ..ansi import ANSI_ESCAPE_RE, clip_ansi_line, slice_ansi_line
from ..symbols import SymbolEntry
from .diffmap import (
    diff_preview_logical_line_is_removed,
    diff_preview_uses_plain_markers,
    iter_diff_logical_line_ranges,
)
from .text import line_has_newline_terminator


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
