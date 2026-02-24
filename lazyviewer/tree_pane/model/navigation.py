"""Tree-entry index navigation helpers."""

from __future__ import annotations

from pathlib import Path

from .types import TreeEntry


def next_file_entry_index(
    entries: list[TreeEntry],
    selected_idx: int,
    direction: int,
) -> int | None:
    """Return next non-directory entry index in the requested direction."""
    if not entries or direction == 0:
        return None
    step = 1 if direction > 0 else -1
    idx = selected_idx + step
    while 0 <= idx < len(entries):
        if not entries[idx].is_dir:
            return idx
        idx += step
    return None


def next_directory_entry_index(
    entries: list[TreeEntry],
    selected_idx: int,
    direction: int,
) -> int | None:
    """Return next directory entry index in the requested direction."""
    if not entries or direction == 0:
        return None
    step = 1 if direction > 0 else -1
    idx = selected_idx + step
    while 0 <= idx < len(entries):
        if entries[idx].is_dir:
            return idx
        idx += step
    return None


def next_opened_directory_entry_index(
    entries: list[TreeEntry],
    selected_idx: int,
    direction: int,
    expanded: set[Path],
) -> int | None:
    """Return next expanded-directory entry index in the requested direction."""
    if not entries or direction == 0:
        return None
    step = 1 if direction > 0 else -1
    idx = selected_idx + step
    while 0 <= idx < len(entries):
        entry = entries[idx]
        if entry.is_dir and entry.path.resolve() in expanded:
            return idx
        idx += step
    return None


def next_index_after_directory_subtree(entries: list[TreeEntry], directory_idx: int) -> int | None:
    """Return first index after the subtree rooted at ``directory_idx``."""
    if not entries or directory_idx < 0 or directory_idx >= len(entries):
        return None
    directory_entry = entries[directory_idx]
    if not directory_entry.is_dir:
        return None

    idx = directory_idx + 1
    while idx < len(entries) and entries[idx].depth > directory_entry.depth:
        idx += 1
    if idx >= len(entries):
        return None
    return idx
