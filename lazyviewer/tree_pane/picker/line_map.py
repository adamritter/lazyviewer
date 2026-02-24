"""Helpers that map rendered display rows to source line numbers."""

from __future__ import annotations


def line_has_newline_terminator(line: str) -> bool:
    """Return whether a rendered display fragment ends a source line."""
    return line.endswith("\n") or line.endswith("\r")


def source_line_for_display_index(lines: list[str], display_index: int) -> int:
    """Map a rendered-line index back to 1-based source line numbering."""
    if not lines:
        return 1

    clamped = max(0, min(display_index, len(lines) - 1))
    source_line = 1
    for idx in range(clamped):
        if line_has_newline_terminator(lines[idx]):
            source_line += 1
    return source_line


def first_display_index_for_source_line(lines: list[str], source_line: int) -> int:
    """Return the first rendered-line index corresponding to ``source_line``."""
    if not lines:
        return 0

    target = max(1, source_line)
    current_source = 1
    for idx, line in enumerate(lines):
        if current_source >= target:
            return idx
        if line_has_newline_terminator(line):
            current_source += 1
    return len(lines) - 1
