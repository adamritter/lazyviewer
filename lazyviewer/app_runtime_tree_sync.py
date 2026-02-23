"""Tree selection and refresh synchronization helpers for app runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .state import AppState


@dataclass(frozen=True)
class _PreviewSelectionDeps:
    state: AppState
    clear_source_selection: Callable[[], bool]
    refresh_rendered_for_current_path: Callable[..., None]
    jump_to_line: Callable[[int], None]


def _preview_selected_entry(
    deps: _PreviewSelectionDeps,
    force: bool = False,
) -> None:
    state = deps.state
    if not state.tree_entries:
        return
    entry = state.tree_entries[state.selected_idx]
    selected_target = entry.path.resolve()
    if deps.clear_source_selection():
        state.dirty = True
    if entry.kind == "search_hit":
        if force or selected_target != state.current_path.resolve():
            state.current_path = selected_target
            deps.refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
        if entry.line is not None:
            deps.jump_to_line(max(0, entry.line - 1))
        return
    if not force and selected_target == state.current_path.resolve():
        return
    state.current_path = selected_target
    deps.refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)


@dataclass(frozen=True)
class _TreeRefreshSyncDeps:
    state: AppState
    rebuild_tree_entries: Callable[..., None]
    refresh_rendered_for_current_path: Callable[..., None]
    schedule_tree_filter_index_warmup: Callable[..., None]
    refresh_git_status_overlay: Callable[..., None]


def _sync_selected_target_after_tree_refresh(
    deps: _TreeRefreshSyncDeps,
    preferred_path: Path,
    force_rebuild: bool = False,
) -> None:
    state = deps.state
    previous_current_path = state.current_path.resolve()
    deps.rebuild_tree_entries(preferred_path=preferred_path)
    if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries):
        selected_target = state.tree_entries[state.selected_idx].path.resolve()
    else:
        selected_target = state.tree_root.resolve()

    changed_target = selected_target != previous_current_path
    if changed_target:
        state.current_path = selected_target
    deps.refresh_rendered_for_current_path(
        reset_scroll=changed_target,
        reset_dir_budget=changed_target,
        force_rebuild=force_rebuild,
    )
    deps.schedule_tree_filter_index_warmup()
    deps.refresh_git_status_overlay(force=True)
    state.dirty = True

