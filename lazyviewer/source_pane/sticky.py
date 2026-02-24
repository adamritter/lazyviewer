"""Sticky-symbol scope detection and header composition.

Sticky headers mirror the active symbol chain (for example, nested class/
function context) at the top of the preview pane. This module decides when a
symbol scope ends across blank lines/indent transitions and builds the header
set shown for both normal source views and git-diff previews.
"""

from __future__ import annotations

from pathlib import Path

from ..render.ansi import ANSI_ESCAPE_RE
from .syntax import read_text
from ..source_pane.symbols import SymbolEntry, collect_sticky_symbol_headers, next_symbol_start_line
from .diffmap import diff_source_line_for_display_index
from .source import (
    next_nonblank_source_line,
    source_line_is_blank,
    source_line_raw_text,
    status_line_range,
    sticky_source_lines,
)
from .text import format_sticky_header_line


def leading_indent_columns(text: str) -> int:
    """Return indentation width where tabs count as four columns."""
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
    """Decide whether a blank line terminates the current sticky symbol scope.

    Blank lines are considered inside scope when the next nonblank line is more
    indented than the sticky header (or is an isolated closing brace), and
    outside scope when control clearly returns to sibling/parent level.
    """
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
    """Return whether ``source_line`` lies outside ``sticky_symbol`` scope."""
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
    """Compute sticky symbol chain for the current viewport top.

    For git diff previews, scope checks prefer raw source-file text to avoid
    repeated diff-to-source remapping while scrolling.
    """
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
        """Filter sticky candidates to symbols still active at ``source_line``."""
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


def formatted_sticky_headers(
    text_lines: list[str],
    sticky_symbols: list[SymbolEntry],
    width: int,
    wrap_text: bool,
    text_x: int,
    preview_is_git_diff: bool = False,
) -> list[str]:
    """Render sticky symbol source lines into formatted header rows."""
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
