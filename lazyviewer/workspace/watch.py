"""Polling policy for revisioned multi-root workspace snapshots."""

from __future__ import annotations

from dataclasses import dataclass

from .model import WorkspaceDelta, WorkspaceQuery, WorkspaceSnapshot
from .service import WorkspaceService


@dataclass(slots=True)
class WorkspaceWatcher:
    """Coordinate tree and Git polling without depending on application state."""

    service: WorkspaceService
    snapshot: WorkspaceSnapshot
    tree_poll_seconds: float
    git_poll_seconds: float
    tree_last_poll: float = 0.0
    git_last_poll: float = 0.0
    _tree_dirty: bool = False
    _git_dirty: bool = False

    def mark_tree_dirty(self) -> None:
        self._tree_dirty = True

    def mark_git_dirty(self) -> None:
        self._git_dirty = True

    def _refresh(
        self,
        query: WorkspaceQuery,
        *,
        refresh_git_status: bool,
    ) -> WorkspaceDelta:
        delta = self.service.refresh(
            self.snapshot,
            query,
            refresh_git_status=refresh_git_status,
        )
        self.snapshot = delta.snapshot
        return delta

    def poll_tree(self, query: WorkspaceQuery, now: float) -> WorkspaceDelta | None:
        """Poll all workspace roots for visible tree changes when due."""
        query_changed = query != self.snapshot.query
        if not query_changed and not self._tree_dirty and (now - self.tree_last_poll) < self.tree_poll_seconds:
            return None
        self.tree_last_poll = now
        self._tree_dirty = False
        return self._refresh(query, refresh_git_status=False)

    def poll_git(
        self,
        query: WorkspaceQuery,
        now: float,
        *,
        force: bool = False,
    ) -> WorkspaceDelta | None:
        """Poll Git status for every root, including working-tree-only changes."""
        query_changed = query != self.snapshot.query
        if (
            not force
            and not query_changed
            and not self._git_dirty
            and (now - self.git_last_poll) < self.git_poll_seconds
        ):
            return None
        self.git_last_poll = now
        self._git_dirty = False
        delta = self.service.refresh_git(self.snapshot, query)
        self.snapshot = delta.snapshot
        return delta
