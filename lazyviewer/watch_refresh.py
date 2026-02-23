"""Watch and git-overlay refresh helpers for app runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .state import AppState


@dataclass
class _WatchRefreshContext:
    tree_last_poll: float = 0.0
    tree_signature: str | None = None
    git_last_poll: float = 0.0
    git_signature: str | None = None
    git_repo_root: Path | None = None
    git_dir: Path | None = None


def _refresh_git_status_overlay(
    state: AppState,
    refresh_rendered_for_current_path: Callable[..., None],
    *,
    collect_git_status_overlay: Callable[[Path], dict[Path, int]],
    monotonic: Callable[[], float],
    status_refresh_seconds: float,
    force: bool = False,
) -> None:
    if not state.git_features_enabled:
        if state.git_status_overlay:
            state.git_status_overlay = {}
            state.dirty = True
        state.git_status_last_refresh = monotonic()
        return

    now = monotonic()
    if not force and (now - state.git_status_last_refresh) < status_refresh_seconds:
        return

    previous = state.git_status_overlay
    state.git_status_overlay = collect_git_status_overlay(state.tree_root)
    state.git_status_last_refresh = monotonic()
    if state.git_status_overlay != previous:
        if state.current_path.resolve().is_dir():
            refresh_rendered_for_current_path(reset_scroll=False, reset_dir_budget=False)
        state.dirty = True


def _reset_git_watch_context(
    state: AppState,
    watch_context: _WatchRefreshContext,
    *,
    resolve_git_paths: Callable[[Path], tuple[Path | None, Path | None]],
) -> None:
    watch_context.git_repo_root, watch_context.git_dir = resolve_git_paths(state.tree_root)
    watch_context.git_last_poll = 0.0
    watch_context.git_signature = None


def _maybe_refresh_tree_watch(
    state: AppState,
    watch_context: _WatchRefreshContext,
    sync_selected_target_after_tree_refresh: Callable[..., None],
    *,
    build_tree_watch_signature: Callable[[Path, set[Path], bool], str],
    monotonic: Callable[[], float],
    tree_watch_poll_seconds: float,
) -> None:
    now = monotonic()
    if (now - watch_context.tree_last_poll) < tree_watch_poll_seconds:
        return
    watch_context.tree_last_poll = now

    signature = build_tree_watch_signature(
        state.tree_root,
        state.expanded,
        state.show_hidden,
    )
    if watch_context.tree_signature is None:
        watch_context.tree_signature = signature
        return
    if signature == watch_context.tree_signature:
        return

    watch_context.tree_signature = signature
    preferred_path = (
        state.tree_entries[state.selected_idx].path.resolve()
        if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries)
        else state.current_path.resolve()
    )
    sync_selected_target_after_tree_refresh(preferred_path=preferred_path)


def _maybe_refresh_git_watch(
    state: AppState,
    watch_context: _WatchRefreshContext,
    refresh_git_status_overlay: Callable[..., None],
    refresh_rendered_for_current_path: Callable[..., None],
    *,
    build_git_watch_signature: Callable[[Path | None], str],
    monotonic: Callable[[], float],
    git_watch_poll_seconds: float,
) -> None:
    if not state.git_features_enabled:
        return
    now = monotonic()
    if (now - watch_context.git_last_poll) < git_watch_poll_seconds:
        return
    watch_context.git_last_poll = now

    signature = build_git_watch_signature(watch_context.git_dir)
    if watch_context.git_signature is None:
        watch_context.git_signature = signature
        return
    if signature == watch_context.git_signature:
        return

    watch_context.git_signature = signature
    refresh_git_status_overlay(force=True)
    # Git HEAD/index changes can invalidate the current file's diff preview
    # even when the selected path hasn't changed.
    previous_rendered = state.rendered
    previous_start = state.start
    previous_max_start = state.max_start
    refresh_rendered_for_current_path(reset_scroll=False, reset_dir_budget=False)
    if (
        state.rendered != previous_rendered
        or state.start != previous_start
        or state.max_start != previous_max_start
    ):
        state.dirty = True


def _mark_tree_watch_dirty(watch_context: _WatchRefreshContext) -> None:
    watch_context.tree_signature = None

