"""Tree-pane selection/refresh synchronization helpers.

These helpers coordinate selected target preservation across tree rebuilds and
ensure preview content is refreshed consistently after structural changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..session import SessionState
from ..ports import (
    RebuildTreeEntries,
    RefreshGitStatus,
    RefreshPreview,
    RequestDirectoryPreview,
)
from ..tree_model import TreeEntry


@dataclass
class PreviewSelection:
    """Sync preview content with tree selection and optional line jumps."""

    state: SessionState
    clear_source_selection: Callable[[], bool]
    refresh_rendered_for_current_path: RefreshPreview
    request_directory_preview_async: RequestDirectoryPreview | None = None
    jump_to_line: Callable[[int], None] | None = None

    def bind_jump_to_line(self, jump_to_line: Callable[[int], None]) -> None:
        """Attach jump callback once navigation wiring is available."""
        self.jump_to_line = jump_to_line

    def reveal_entry_line(self, entry: TreeEntry) -> None:
        """Reveal a committed content-search hit after its preview is applied."""
        if entry.kind == "search_hit" and entry.line is not None and self.jump_to_line is not None:
            self.jump_to_line(max(0, entry.line - 1))

    def _commit_entry(self, entry: TreeEntry) -> None:
        state = self.state
        state.workspace.current_path = entry.path.resolve()
        if entry.kind == "search_hit":
            state.preview.search_current_line = entry.line or 0
            state.preview.search_current_column = entry.column or 0
        else:
            state.preview.search_current_line = 0
            state.preview.search_current_column = 0
        if self.clear_source_selection():
            state.interface.dirty = True

    def preview_selected_entry(
        self,
        force: bool = False,
    ) -> None:
        """Update current preview target from selected tree entry."""
        state = self.state
        if not state.workspace.entries:
            state.preview.search_current_line = 0
            state.preview.search_current_column = 0
            return
        entry = state.workspace.entries[state.workspace.selected]
        selected_target = entry.path.resolve()
        previous_target = state.workspace.current_path.resolve()
        self._commit_entry(entry)
        if entry.kind == "search_hit":
            if force or selected_target != previous_target:
                self.refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
            self.reveal_entry_line(entry)
            return
        if not force and selected_target == previous_target:
            return
        if (
            not force
            and entry.is_dir
            and self.request_directory_preview_async is not None
        ):
            self.request_directory_preview_async(
                selected_target,
                reset_scroll=True,
                reset_dir_budget=True,
            )
            return
        self.refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)


@dataclass(frozen=True)
class TreeRefreshSync:
    """Dependencies for reconciling selected path after tree rebuilds."""

    state: SessionState
    rebuild_tree_entries: RebuildTreeEntries
    refresh_rendered_for_current_path: RefreshPreview
    schedule_tree_filter_index_warmup: Callable[[], None]
    refresh_git_status_overlay: RefreshGitStatus

    def sync_selected_target_after_tree_refresh(
        self,
        preferred_path: Path,
        force_rebuild: bool = False,
    ) -> None:
        """Rebuild tree, refresh preview, and run follow-up side effects."""
        state = self.state
        previous_current_path = state.workspace.current_path.resolve()
        self.rebuild_tree_entries(preferred_path=preferred_path)
        if state.workspace.entries and 0 <= state.workspace.selected < len(state.workspace.entries):
            selected_target = state.workspace.entries[state.workspace.selected].path.resolve()
        else:
            selected_target = state.workspace.active_root.resolve()

        changed_target = selected_target != previous_current_path
        if changed_target:
            state.workspace.current_path = selected_target
        self.refresh_rendered_for_current_path(
            reset_scroll=changed_target,
            reset_dir_budget=changed_target,
            force_rebuild=force_rebuild,
        )
        self.schedule_tree_filter_index_warmup()
        self.refresh_git_status_overlay(force=True)
        state.interface.dirty = True
