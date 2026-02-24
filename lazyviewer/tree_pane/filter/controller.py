"""Public composition point for tree-filter operations."""

from __future__ import annotations

from .deps import TreeFilterDeps
from .lifecycle import TreeFilterLifecycleMixin
from .matching import TreeFilterMatchingMixin
from .navigation import TreeFilterNavigationMixin


class TreeFilterOps(
    TreeFilterLifecycleMixin,
    TreeFilterNavigationMixin,
    TreeFilterMatchingMixin,
):
    """Stateful controller for tree filter lifecycle and navigation."""

    def __init__(self, deps: TreeFilterDeps) -> None:
        """Create operations object bound to shared runtime state."""
        self.state = deps.state
        self.visible_content_rows = deps.visible_content_rows
        self.rebuild_screen_lines = deps.rebuild_screen_lines
        self.preview_selected_entry = deps.preview_selected_entry
        self.current_jump_location = deps.current_jump_location
        self.record_jump_if_changed = deps.record_jump_if_changed
        self.jump_to_path = deps.jump_to_path
        self.jump_to_line = deps.jump_to_line
        self.on_tree_filter_state_change = deps.on_tree_filter_state_change
        self.loading_until = 0.0
        self.init_content_search_cache()
