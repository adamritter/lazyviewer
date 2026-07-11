"""Revisioned workspace observation and file-index service."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path

from .signatures import (
    build_git_watch_signature,
    build_tree_watch_signature,
    resolve_git_paths,
)
from ..git_status import collect_git_status_overlay
from .index import build_workspace_file_index
from .model import (
    WorkspaceDelta,
    WorkspaceFileIndex,
    WorkspaceQuery,
    WorkspaceRevision,
    WorkspaceRootSnapshot,
    WorkspaceSnapshot,
)

TreeSignatureBuilder = Callable[[Path, set[Path], bool], str]
GitPathResolver = Callable[[Path], tuple[Path | None, Path | None]]
GitSignatureBuilder = Callable[[Path | None], str]
GitStatusCollector = Callable[[Path], dict[Path, int]]
FileIndexBuilder = Callable[[WorkspaceSnapshot], WorkspaceFileIndex]


def _revision_digest(tokens: list[str]) -> str:
    digest = hashlib.blake2b(digest_size=20)
    for token in tokens:
        digest.update(token.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
    return digest.hexdigest()


class WorkspaceService:
    """Own workspace observations and caches keyed by immutable revisions."""

    def __init__(
        self,
        *,
        build_tree_signature: TreeSignatureBuilder = build_tree_watch_signature,
        resolve_git_context: GitPathResolver = resolve_git_paths,
        build_git_signature: GitSignatureBuilder = build_git_watch_signature,
        collect_git_status: GitStatusCollector | None = None,
        build_file_index: FileIndexBuilder = build_workspace_file_index,
    ) -> None:
        self._build_tree_signature = build_tree_signature
        self._resolve_git_context = resolve_git_context
        self._build_git_signature = build_git_signature
        self._collect_git_status = collect_git_status or collect_git_status_overlay
        self._build_file_index = build_file_index
        self._file_indexes: dict[str, WorkspaceFileIndex] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _revision(query: WorkspaceQuery, roots: tuple[WorkspaceRootSnapshot, ...]) -> WorkspaceRevision:
        tree_tokens = [
            f"hidden:{int(query.show_hidden)}",
            f"ignored:{int(query.skip_gitignored)}",
        ]
        git_tokens: list[str] = []
        for section, root in enumerate(roots):
            tree_tokens.extend(
                [
                    f"section:{section}",
                    f"root:{root.path}",
                    f"expanded:{','.join(sorted(str(path) for path in root.expanded))}",
                    f"tree:{root.tree_signature}",
                ]
            )
            git_tokens.extend(
                [
                    f"section:{section}",
                    f"root:{root.path}",
                    f"git:{root.git_signature}",
                    f"status:{repr(root.git_status)}",
                ]
            )
        return WorkspaceRevision(
            tree=_revision_digest(tree_tokens),
            git=_revision_digest(git_tokens),
        )

    def snapshot(self, query: WorkspaceQuery) -> WorkspaceSnapshot:
        """Observe every root and collect one Git overlay per root."""
        root_snapshots: list[WorkspaceRootSnapshot] = []
        for section, root in enumerate(query.roots):
            expanded = query.expanded_by_root[section]
            tree_signature = self._build_tree_signature(root, set(expanded), query.show_hidden)
            git_repo_root, git_dir = self._resolve_git_context(root)
            git_signature = self._build_git_signature(git_dir)
            overlay = self._collect_git_status(root)
            root_snapshots.append(
                WorkspaceRootSnapshot(
                    path=root,
                    expanded=expanded,
                    tree_signature=tree_signature,
                    git_repo_root=git_repo_root,
                    git_dir=git_dir,
                    git_signature=git_signature,
                    git_status=tuple(sorted(overlay.items(), key=lambda item: str(item[0]))),
                )
            )
        roots = tuple(root_snapshots)
        return WorkspaceSnapshot(query=query, roots=roots, revision=self._revision(query, roots))

    def refresh(
        self,
        previous: WorkspaceSnapshot,
        query: WorkspaceQuery,
        *,
        refresh_git_status: bool = False,
    ) -> WorkspaceDelta:
        """Refresh all roots and optionally re-query working-tree Git status."""
        query_changed = query != previous.query
        refreshed_roots: list[WorkspaceRootSnapshot] = []
        tree_changed: list[int] = []
        git_changed: list[int] = []

        for section, root in enumerate(query.roots):
            expanded = query.expanded_by_root[section]
            old = previous.roots[section] if section < len(previous.roots) else None
            tree_signature = self._build_tree_signature(root, set(expanded), query.show_hidden)
            git_repo_root, git_dir = self._resolve_git_context(root)
            git_signature = self._build_git_signature(git_dir)

            same_root = old is not None and old.path == root
            section_tree_changed = (
                query_changed
                or not same_root
                or old.expanded != expanded
                or old.tree_signature != tree_signature
            )
            git_context_changed = (
                query_changed
                or not same_root
                or old.git_dir != git_dir
                or old.git_signature != git_signature
            )
            if section_tree_changed:
                tree_changed.append(section)
            if git_context_changed or refresh_git_status:
                overlay = self._collect_git_status(root)
                git_status = tuple(sorted(overlay.items(), key=lambda item: str(item[0])))
            else:
                assert old is not None
                git_status = old.git_status
            section_git_changed = git_context_changed or old is None or git_status != old.git_status
            if section_git_changed:
                git_changed.append(section)
            refreshed_roots.append(
                WorkspaceRootSnapshot(
                    path=root,
                    expanded=expanded,
                    tree_signature=tree_signature,
                    git_repo_root=git_repo_root,
                    git_dir=git_dir,
                    git_signature=git_signature,
                    git_status=git_status,
                )
            )

        if len(previous.roots) > len(query.roots):
            removed = range(len(query.roots), len(previous.roots))
            tree_changed.extend(removed)
            git_changed.extend(removed)

        roots = tuple(refreshed_roots)
        snapshot = WorkspaceSnapshot(query=query, roots=roots, revision=self._revision(query, roots))
        if snapshot.revision.tree != previous.revision.tree:
            self.invalidate_file_indexes(except_revision=snapshot.revision.tree)
        return WorkspaceDelta(
            snapshot=snapshot,
            tree_changed_sections=tuple(tree_changed),
            git_changed_sections=tuple(git_changed),
        )

    def refresh_git(
        self,
        previous: WorkspaceSnapshot,
        query: WorkspaceQuery,
    ) -> WorkspaceDelta:
        """Refresh only Git observations while preserving the tree revision."""
        if query != previous.query:
            return self.refresh(previous, query, refresh_git_status=True)

        refreshed: list[WorkspaceRootSnapshot] = []
        git_changed: list[int] = []
        for section, old in enumerate(previous.roots):
            root = query.roots[section]
            git_repo_root, git_dir = self._resolve_git_context(root)
            git_signature = self._build_git_signature(git_dir)
            overlay = self._collect_git_status(root)
            git_status = tuple(sorted(overlay.items(), key=lambda item: str(item[0])))
            if (
                old.git_repo_root != git_repo_root
                or old.git_dir != git_dir
                or old.git_signature != git_signature
                or old.git_status != git_status
            ):
                git_changed.append(section)
            refreshed.append(
                WorkspaceRootSnapshot(
                    path=old.path,
                    expanded=old.expanded,
                    tree_signature=old.tree_signature,
                    git_repo_root=git_repo_root,
                    git_dir=git_dir,
                    git_signature=git_signature,
                    git_status=git_status,
                )
            )

        roots = tuple(refreshed)
        snapshot = WorkspaceSnapshot(
            query=query,
            roots=roots,
            revision=self._revision(query, roots),
        )
        return WorkspaceDelta(
            snapshot=snapshot,
            tree_changed_sections=(),
            git_changed_sections=tuple(git_changed),
        )

    def file_index(self, snapshot: WorkspaceSnapshot, *, refresh: bool = False) -> WorkspaceFileIndex:
        """Return the revision-keyed file catalog for a workspace snapshot."""
        key = snapshot.revision.tree
        with self._lock:
            cached = None if refresh else self._file_indexes.get(key)
        if cached is not None:
            return cached
        built = self._build_file_index(snapshot)
        with self._lock:
            self._file_indexes[key] = built
        return built

    def invalidate_file_indexes(self, *, except_revision: str | None = None) -> None:
        """Drop obsolete file catalogs after a workspace revision change."""
        with self._lock:
            if except_revision is None:
                self._file_indexes.clear()
            else:
                self._file_indexes = {
                    revision: index
                    for revision, index in self._file_indexes.items()
                    if revision == except_revision
                }
