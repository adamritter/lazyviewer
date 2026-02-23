"""Watch and git-overlay refresh helpers for app runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .state import AppState


@dataclass
class WatchRefreshContext:
    tree_last_poll: float = 0.0
    tree_signature: str | None = None
    git_last_poll: float = 0.0
    git_signature: str | None = None
    git_repo_root: Path | None = None
    git_dir: Path | None = None

    def mark_tree_dirty(self) -> None:
        self.tree_signature = None

    def reset_git_context(
        self,
        state: AppState,
        *,
        resolve_git_paths: Callable[[Path], tuple[Path | None, Path | None]],
    ) -> None:
        self.git_repo_root, self.git_dir = resolve_git_paths(state.tree_root)
        self.git_last_poll = 0.0
        self.git_signature = None

    def maybe_refresh_tree(
        self,
        state: AppState,
        sync_selected_target_after_tree_refresh: Callable[..., None],
        *,
        build_tree_watch_signature: Callable[[Path, set[Path], bool], str],
        monotonic: Callable[[], float],
        tree_watch_poll_seconds: float,
    ) -> None:
        now = monotonic()
        if (now - self.tree_last_poll) < tree_watch_poll_seconds:
            return
        self.tree_last_poll = now

        signature = build_tree_watch_signature(
            state.tree_root,
            state.expanded,
            state.show_hidden,
        )
        if self.tree_signature is None:
            self.tree_signature = signature
            return
        if signature == self.tree_signature:
            return

        self.tree_signature = signature
        preferred_path = (
            state.tree_entries[state.selected_idx].path.resolve()
            if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries)
            else state.current_path.resolve()
        )
        sync_selected_target_after_tree_refresh(preferred_path=preferred_path)

    def maybe_refresh_git(
        self,
        state: AppState,
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
        if (now - self.git_last_poll) < git_watch_poll_seconds:
            return
        self.git_last_poll = now

        signature = build_git_watch_signature(self.git_dir)
        if self.git_signature is None:
            self.git_signature = signature
            return
        if signature == self.git_signature:
            return

        self.git_signature = signature
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
