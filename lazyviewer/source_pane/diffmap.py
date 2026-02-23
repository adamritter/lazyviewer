"""Diff-specific helpers for preview rendering."""

from __future__ import annotations

from ..ansi import ANSI_ESCAPE_RE
from .text import line_has_newline_terminator

DIFF_REMOVED_BG_SGR = "48;2;92;43;49"


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
