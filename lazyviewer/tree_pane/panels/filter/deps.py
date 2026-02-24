"""Dependency container for tree-filter operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ....runtime.navigation import JumpLocation
from ....runtime.state import AppState


@dataclass(frozen=True)
class TreeFilterDeps:
    """Runtime dependencies required by :class:`TreeFilterOps`."""

    state: AppState
    visible_content_rows: Callable[[], int]
    rebuild_screen_lines: Callable[..., None]
    preview_selected_entry: Callable[..., None]
    current_jump_location: Callable[[], JumpLocation]
    record_jump_if_changed: Callable[[JumpLocation], None]
    jump_to_path: Callable[[Path], None]
    jump_to_line: Callable[[int], None]
    on_tree_filter_state_change: Callable[[], None] | None = None
