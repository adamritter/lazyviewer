"""ANSI-aware text measurement and line shaping utilities.

Provides clipping, slicing, and wrapping that preserve escape sequences.
These helpers keep rendering aligned when color codes and wide chars are present.
"""

from __future__ import annotations

import re
import unicodedata

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
TAB_STOP = 8


def char_display_width(ch: str, col: int) -> int:
    """Return terminal column width for one character at visual column ``col``.

    Tabs expand to the next 8-column stop, combining marks consume no columns,
    and East Asian wide/fullwidth characters consume two.
    """
    if ch == "\t":
        return TAB_STOP - (col % TAB_STOP)
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in {"W", "F"}:
        return 2
    return 1


def clip_ansi_line(text: str, max_cols: int) -> str:
    """Trim a styled line to at most ``max_cols`` display columns.

    ANSI escape sequences are preserved verbatim and do not count toward width.
    Tabs are expanded into spaces so clipping aligns with rendered terminal cells.
    """
    if max_cols <= 0 or not text:
        return ""

    out: list[str] = []
    col = 0
    i = 0
    n = len(text)
    while i < n and col < max_cols:
        if text[i] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                out.append(match.group(0))
                i = match.end()
                continue
        ch = text[i]
        w = char_display_width(ch, col)
        if ch == "\t":
            if col + w > max_cols:
                break
            out.append(" " * w)
            col += w
            i += 1
            continue
        if col + w > max_cols:
            break
        out.append(ch)
        col += w
        i += 1

    return "".join(out)


def slice_ansi_line(text: str, start_cols: int, max_cols: int) -> str:
    """Return a horizontal viewport of a styled line.

    The slice starts at ``start_cols`` display columns and includes up to
    ``max_cols`` columns. If the viewport begins after a color/style sequence,
    the latest pending SGR sequence is injected so visible text keeps the
    original styling.
    """
    if max_cols <= 0 or not text:
        return ""
    if start_cols < 0:
        start_cols = 0

    out: list[str] = []
    col = 0
    shown = 0
    i = 0
    n = len(text)
    pending_sgr = ""
    injected_style = False
    while i < n and shown < max_cols:
        if text[i] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                seq = match.group(0)
                is_sgr = seq.endswith("m")
                if is_sgr:
                    pending_sgr = seq
                if col >= start_cols:
                    if not injected_style and pending_sgr and not is_sgr:
                        out.append(pending_sgr)
                        injected_style = True
                    out.append(seq)
                    if is_sgr:
                        injected_style = True
                i = match.end()
                continue
        ch = text[i]
        w = char_display_width(ch, col)
        if col + w <= start_cols:
            col += w
            i += 1
            continue
        if not injected_style and pending_sgr:
            out.append(pending_sgr)
            injected_style = True
        if ch == "\t":
            spaces = " " * w
            for sp in spaces:
                if shown >= max_cols:
                    break
                out.append(sp)
                shown += 1
            col += w
            i += 1
            continue
        if shown + w > max_cols:
            break
        out.append(ch)
        shown += w
        col += w
        i += 1

    return "".join(out)


def wrap_ansi_line(text: str, width: int) -> list[str]:
    """Wrap a styled line into chunks that fit ``width`` display columns.

    Escape sequences remain attached to their surrounding chunk, and tab
    expansion respects terminal tab-stop alignment for each wrapped segment.
    """
    if width <= 0:
        return [""]
    if not text:
        return [""]

    wrapped: list[str] = []
    chunk: list[str] = []
    col = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                chunk.append(match.group(0))
                i = match.end()
                continue

        if col >= width:
            wrapped.append("".join(chunk))
            chunk = []
            col = 0

        ch = text[i]
        w = char_display_width(ch, col)
        if ch == "\t":
            if col + w > width and chunk:
                wrapped.append("".join(chunk))
                chunk = []
                col = 0
                w = TAB_STOP
            chunk.append(" " * w)
            col += w
            i += 1
            continue
        if col + w > width and chunk:
            wrapped.append("".join(chunk))
            chunk = []
            col = 0
        chunk.append(ch)
        col += char_display_width(ch, col)
        i += 1

    wrapped.append("".join(chunk))
    return wrapped


def build_screen_lines(rendered: str, width: int, wrap: bool = False) -> list[str]:
    """Split rendered output into displayable screen lines.

    When ``wrap`` is false, this mirrors ``splitlines(keepends=True)``. When
    true, each logical line body is wrapped with :func:`wrap_ansi_line` and the
    original newline terminator is appended to the final wrapped chunk.
    """
    lines = rendered.splitlines(keepends=True)
    if not lines:
        return [""]
    if not wrap:
        return lines

    wrapped: list[str] = []
    for line in lines:
        newline = ""
        if line.endswith("\r\n"):
            body = line[:-2]
            newline = "\r\n"
        elif line.endswith("\n") or line.endswith("\r"):
            body = line[:-1]
            newline = line[-1]
        else:
            body = line
        chunks = wrap_ansi_line(body, width)
        if newline and chunks:
            chunks[-1] = f"{chunks[-1]}{newline}"
        wrapped.extend(chunks)
    return wrapped or [""]
