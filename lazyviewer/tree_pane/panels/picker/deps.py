"""Dependency container for picker/navigation operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ....runtime.state import AppState


@dataclass(frozen=True)
class NavigationPickerDeps:
    """Runtime dependencies required by :class:`NavigationPickerOps`."""

    state: AppState
    command_palette_items: tuple[tuple[str, str], ...]
    rebuild_screen_lines: Callable[..., None]
    rebuild_tree_entries: Callable[..., None]
    preview_selected_entry: Callable[..., None]
    schedule_tree_filter_index_warmup: Callable[[], None]
    mark_tree_watch_dirty: Callable[[], None]
    reset_git_watch_context: Callable[[], None]
    refresh_git_status_overlay: Callable[..., None]
    visible_content_rows: Callable[[], int]
    refresh_rendered_for_current_path: Callable[..., None]
