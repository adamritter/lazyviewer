"""Tree selection and refresh synchronization helpers for app runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..state import AppState


@dataclass(frozen=True)
class PreviewSelectionDeps:
    """Dependencies for syncing preview content with tree selection."""

    state: AppState
    clear_source_selection: Callable[[], bool]
    refresh_rendered_for_current_path: Callable[..., None]
    jump_to_line: Callable[[int], None]

    def preview_selected_entry(
        self,
        force: bool = False,
    ) -> None:
        """Update current preview target from selected tree entry.

        Search-hit entries also jump to their matched source line after preview
        content is refreshed.
        """
        state = self.state
        if not state.tree_entries:
            return
        entry = state.tree_entries[state.selected_idx]
        selected_target = entry.path.resolve()
        if self.clear_source_selection():
            state.dirty = True
        if entry.kind == "search_hit":
            if force or selected_target != state.current_path.resolve():
                state.current_path = selected_target
                self.refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
            if entry.line is not None:
                self.jump_to_line(max(0, entry.line - 1))
            return
        if not force and selected_target == state.current_path.resolve():
            return
        state.current_path = selected_target
        self.refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)


@dataclass(frozen=True)
class TreeRefreshSyncDeps:
    """Dependencies for reconciling selected path after tree rebuilds."""

    state: AppState
    rebuild_tree_entries: Callable[..., None]
    refresh_rendered_for_current_path: Callable[..., None]
    schedule_tree_filter_index_warmup: Callable[..., None]
    refresh_git_status_overlay: Callable[..., None]

    def sync_selected_target_after_tree_refresh(
        self,
        preferred_path: Path,
        force_rebuild: bool = False,
    ) -> None:
        """Rebuild tree, refresh preview, and kick follow-up warm/overlay work."""
        state = self.state
        previous_current_path = state.current_path.resolve()
        self.rebuild_tree_entries(preferred_path=preferred_path)
        if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries):
            selected_target = state.tree_entries[state.selected_idx].path.resolve()
        else:
            selected_target = state.tree_root.resolve()

        changed_target = selected_target != previous_current_path
        if changed_target:
            state.current_path = selected_target
        self.refresh_rendered_for_current_path(
            reset_scroll=changed_target,
            reset_dir_budget=changed_target,
            force_rebuild=force_rebuild,
        )
        self.schedule_tree_filter_index_warmup()
        self.refresh_git_status_overlay(force=True)
        state.dirty = True
