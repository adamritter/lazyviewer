"""Shared pure helpers for app runtime behavior."""

from __future__ import annotations

from pathlib import Path

from .ansi import ANSI_ESCAPE_RE


def _line_has_git_change_marker(line: str) -> bool:
    """Detect whether a rendered diff line represents an added/removed change."""
    plain = ANSI_ESCAPE_RE.sub("", line)
    if plain.startswith("+ ") or plain.startswith("- "):
        return True
    for match in ANSI_ESCAPE_RE.finditer(line):
        seq = match.group(0)
        if not seq.endswith("m"):
            continue
        if seq.startswith("\x1b[48;") or ";48;" in seq:
            return True
    return False


def _git_change_block_start_lines(screen_lines: list[str]) -> list[int]:
    """Return start indices for contiguous runs of git change-marked lines."""
    starts: list[int] = []
    in_block = False
    for idx, line in enumerate(screen_lines):
        is_change = _line_has_git_change_marker(line)
        if is_change and not in_block:
            starts.append(idx)
        in_block = is_change
    return starts


def _first_git_change_screen_line(screen_lines: list[str]) -> int | None:
    """Return the first visible git-change block start, if any."""
    starts = _git_change_block_start_lines(screen_lines)
    if not starts:
        return None
    return starts[0]


def _centered_scroll_start(target_line: int, max_start: int, visible_rows: int) -> int:
    """Compute a scroll start that keeps target near upper-middle viewport."""
    anchor = max(0, min(target_line, max_start))
    centered = max(0, anchor - max(1, visible_rows // 3))
    return max(0, min(centered, max_start))


def _tree_order_key_for_relative_path(
    relative_path: Path,
    is_dir: bool = False,
) -> tuple[tuple[int, str, str], ...]:
    """Build a stable sort key matching tree ordering semantics.

    Intermediate path components are treated as directories; for the terminal
    component ``is_dir`` controls whether directories sort before files.
    """
    parts = relative_path.parts
    if not parts:
        return tuple()

    out: list[tuple[int, str, str]] = []
    last_index = len(parts) - 1
    for idx, part in enumerate(parts):
        if idx < last_index:
            node_kind = 0
        else:
            node_kind = 0 if is_dir else 1
        out.append((node_kind, part.casefold(), part))
    return tuple(out)
