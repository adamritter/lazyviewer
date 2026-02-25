"""Pure screen-analysis helpers shared across runtime navigation features.

These functions intentionally avoid ``AppState`` mutation. They inspect
rendered lines and paths to compute anchors and ordering keys used by git-jump
navigation and initial diff-preview placement.
"""

from __future__ import annotations

from pathlib import Path

from ..render.ansi import ANSI_ESCAPE_RE

_DIFF_CHANGE_BG_TOKENS = (
    "48;2;36;74;52",
    "48;2;92;43;49",
)


def _line_has_git_change_marker(line: str) -> bool:
    """Detect whether a rendered diff line represents an added/removed change."""
    plain = ANSI_ESCAPE_RE.sub("", line)
    if plain.startswith("+ ") or plain.startswith("- "):
        return True
    return any(token in line for token in _DIFF_CHANGE_BG_TOKENS)


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
    desired_start = max(0, target_line - max(1, visible_rows // 3))
    return max(0, min(desired_start, max_start))


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
