"""Sticky-header selection helpers built on collected symbols."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .symbols_types import SymbolEntry


def leading_indent_columns(text: str) -> int:
    """Return leading indentation width where tabs count as four columns."""
    count = 0
    for ch in text:
        if ch == " ":
            count += 1
            continue
        if ch == "\t":
            count += 4
            continue
        break
    return count


def enclosing_sticky_symbol_chain(
    candidates: list[SymbolEntry],
    source_lines: list[str],
) -> list[SymbolEntry]:
    """Reduce candidates to indentation-based enclosing symbol stack."""
    if not candidates:
        return []

    if not source_lines:
        return candidates

    stack: list[tuple[SymbolEntry, int]] = []
    for symbol in candidates:
        line_index = symbol.line
        if 0 <= line_index < len(source_lines):
            indent = leading_indent_columns(source_lines[line_index])
        else:
            indent = 0

        while stack and indent <= stack[-1][1]:
            stack.pop()
        stack.append((symbol, indent))

    return [symbol for symbol, _indent in stack]


def collect_sticky_symbol_headers(
    path: Path,
    visible_start_line: int,
    max_headers: int,
    collect_symbols_cached: Callable[[Path, int], tuple[list[SymbolEntry], str | None]],
    read_text_fn: Callable[[Path], str],
) -> list[SymbolEntry]:
    """Return enclosing class/function headers above the visible top line."""
    if max_headers <= 0:
        return []
    start_line = max(1, int(visible_start_line))
    if start_line <= 1:
        return []

    symbols, error = collect_symbols_cached(path, 4000)
    if error is not None or not symbols:
        return []

    all_candidates = [
        symbol
        for symbol in symbols
        if symbol.kind in {"class", "fn"} and (symbol.line + 1) < start_line
    ]
    if not all_candidates:
        return []

    try:
        source_lines = read_text_fn(path).splitlines()
    except Exception:
        source_lines = []

    chain = enclosing_sticky_symbol_chain(all_candidates, source_lines)
    if not chain:
        return []

    if max_headers >= len(chain):
        return chain
    return chain[-max_headers:]


def next_symbol_start_line(
    path: Path,
    after_line: int,
    collect_symbols_cached: Callable[[Path, int], tuple[list[SymbolEntry], str | None]],
) -> int | None:
    """Return next class/function declaration line (1-based) after ``after_line``."""
    start_line = max(1, int(after_line))
    symbols, error = collect_symbols_cached(path, 4000)
    if error is not None or not symbols:
        return None

    for symbol in symbols:
        symbol_line = symbol.line + 1
        if symbol.kind in {"class", "fn"} and symbol_line > start_line:
            return symbol_line
    return None
