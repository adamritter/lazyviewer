"""Low-level text and ANSI utilities for preview rendering."""

from __future__ import annotations

from ..ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line


def plain_display_width(text: str) -> int:
    """Return terminal display width for plain text (no ANSI stripping)."""
    return sum(char_display_width(ch, 0) for ch in text)


def ansi_display_width(text: str) -> int:
    """Return display width after removing ANSI escape sequences."""
    return plain_display_width(ANSI_ESCAPE_RE.sub("", text))


def underline_with_ansi(text: str) -> str:
    """Underline text while preserving existing ANSI color/style sequences.

    For SGR sequences the underline attribute is re-applied after each style
    reset/change so nested colorized tokens remain underlined end-to-end.
    """
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
    """Format one sticky-header row with underline and filler separator."""
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
    """Return whether the line ends with CR or LF terminator."""
    return line.endswith("\n") or line.endswith("\r")


def scroll_percent(text_start: int, total_lines: int, visible_rows: int) -> float:
    """Compute vertical scroll position as percentage of scrollable range."""
    if total_lines <= 0:
        return 0.0
    max_start = max(0, total_lines - max(1, visible_rows))
    if max_start <= 0:
        return 0.0
    clamped_start = max(0, min(text_start, max_start))
    return (clamped_start / max_start) * 100.0
