"""Public composition point for picker/navigation operations."""

from __future__ import annotations

from collections.abc import Callable

from .deps import NavigationPickerDeps
from .matching import PickerMatchingMixin
from .navigation import NavigationHistoryMixin
from .picker_lifecycle import PickerLifecycleMixin
from .view_actions import ViewActionMixin


class NavigationPickerOps(
    PickerLifecycleMixin,
    NavigationHistoryMixin,
    ViewActionMixin,
    PickerMatchingMixin,
):
    """State-bound picker/navigation operations used by key handlers."""

    def __init__(self, deps: NavigationPickerDeps) -> None:
        """Bind controller methods to app state and injected runtime hooks."""
        self.state = deps.state
        self.command_palette_items = deps.command_palette_items
        self.rebuild_screen_lines = deps.rebuild_screen_lines
        self.rebuild_tree_entries = deps.rebuild_tree_entries
        self.preview_selected_entry = deps.preview_selected_entry
        self.schedule_tree_filter_index_warmup = deps.schedule_tree_filter_index_warmup
        self.mark_tree_watch_dirty = deps.mark_tree_watch_dirty
        self.reset_git_watch_context = deps.reset_git_watch_context
        self.refresh_git_status_overlay = deps.refresh_git_status_overlay
        self.visible_content_rows = deps.visible_content_rows
        self.refresh_rendered_for_current_path = deps.refresh_rendered_for_current_path
        self.open_tree_filter_fn: Callable[[str], None] | None = None

    def set_open_tree_filter(self, callback: Callable[[str], None]) -> None:
        """Register callback used by command palette filter/search actions."""
        self.open_tree_filter_fn = callback
