"""Canonical filesystem tree model for a workspace root."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from ..gitignore import get_gitignore_matcher
from .doc_summary import cached_top_file_doc_summary


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: Path
    file_size: int | None = None
    mtime_ns: int | None = None
    git_status_flags: int = 0
    doc_summary: str | None = None


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    path: Path
    mtime_ns: int | None = None
    git_status_flags: int = 0
    children: tuple["WorkspaceTreeEntry", ...] = ()


WorkspaceTreeEntry: TypeAlias = DirectoryEntry | FileEntry


@dataclass(frozen=True, slots=True)
class DirectoryChild:
    """One observed child with metadata shared by tree and directory preview."""

    name: str
    path: Path
    is_dir: bool
    file_size: int | None
    mtime_ns: int | None
    git_status_flags: int = 0
    doc_summary: str | None = None


def safe_file_size(path: Path, is_dir: bool) -> int | None:
    if is_dir:
        return None
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


def safe_mtime_ns(path: Path) -> int | None:
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return None


def maybe_gitignore_matcher(root: Path, skip_gitignored: bool) -> object | None:
    return get_gitignore_matcher(root) if skip_gitignored else None


def list_directory_children(
    directory: Path,
    show_hidden: bool,
    ignore_matcher: object | None = None,
    git_status_overlay: dict[Path, int] | None = None,
    doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
    include_doc_summaries: bool = False,
    *,
    workspace_root: Path | None = None,
) -> tuple[list[DirectoryChild], Exception | None, int | None]:
    """Observe direct children without allowing resolved paths to escape a root."""
    try:
        resolved_directory = directory.resolve()
    except OSError:
        resolved_directory = directory
    resolved_workspace_root = workspace_root.resolve() if workspace_root is not None else None
    directory_mtime_ns = safe_mtime_ns(resolved_directory)
    summary_provider = doc_summary_for_path
    if summary_provider is None and include_doc_summaries:
        summary_provider = cached_top_file_doc_summary

    children: list[DirectoryChild] = []
    try:
        with os.scandir(directory) as entries:
            for child in entries:
                name = child.name
                if not show_hidden and name.startswith("."):
                    continue
                child_path = Path(child.path)
                try:
                    resolved_child = child_path.resolve()
                except OSError:
                    continue
                if resolved_workspace_root is not None and not resolved_child.is_relative_to(resolved_workspace_root):
                    continue
                if ignore_matcher is not None and ignore_matcher.is_ignored(child_path):
                    continue

                try:
                    is_dir = child.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                file_size: int | None = None
                mtime_ns: int | None = None
                try:
                    stat = child.stat(follow_symlinks=False)
                    mtime_ns = int(stat.st_mtime_ns)
                    if not is_dir:
                        file_size = int(stat.st_size)
                except OSError:
                    pass

                flags = 0
                if git_status_overlay is not None:
                    flags = int(
                        git_status_overlay.get(
                            resolved_child,
                            git_status_overlay.get(child_path, 0),
                        )
                    )
                summary: str | None = None
                if not is_dir and summary_provider is not None:
                    try:
                        summary = summary_provider(child_path, file_size)
                    except OSError:
                        summary = None
                children.append(
                    DirectoryChild(
                        name=name,
                        path=child_path,
                        is_dir=is_dir,
                        file_size=file_size,
                        mtime_ns=mtime_ns,
                        git_status_flags=flags,
                        doc_summary=summary,
                    )
                )
    except (PermissionError, OSError) as exc:
        return [], exc, directory_mtime_ns

    children.sort(key=lambda item: (not item.is_dir, item.name.casefold(), item.name))
    return children, None, directory_mtime_ns


def _normalized_expanded(root: Path, expanded: set[Path]) -> set[Path]:
    normalized: set[Path] = set()
    for candidate in expanded:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_relative_to(root):
            normalized.add(resolved)
    return normalized


def build_workspace_tree(
    root: Path,
    expanded: set[Path],
    show_hidden: bool,
    *,
    skip_gitignored: bool = False,
    git_status_overlay: dict[Path, int] | None = None,
    doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
    include_doc_summaries: bool = False,
) -> DirectoryEntry:
    """Build the canonical lazy tree for one workspace root."""
    root = root.resolve()
    expanded_resolved = _normalized_expanded(root, expanded)
    ignore_matcher = maybe_gitignore_matcher(root, skip_gitignored)

    def directory_node(path: Path, *, include_children: bool) -> DirectoryEntry:
        children: tuple[WorkspaceTreeEntry, ...] = ()
        if include_children:
            children = build_children(path)
        return DirectoryEntry(
            path=path,
            mtime_ns=safe_mtime_ns(path),
            git_status_flags=int(git_status_overlay.get(path, 0)) if git_status_overlay else 0,
            children=children,
        )

    def build_children(directory: Path) -> tuple[WorkspaceTreeEntry, ...]:
        observed, scan_error, _mtime = list_directory_children(
            directory,
            show_hidden,
            ignore_matcher=ignore_matcher,
            git_status_overlay=git_status_overlay,
            doc_summary_for_path=doc_summary_for_path,
            include_doc_summaries=include_doc_summaries,
            workspace_root=root,
        )
        if scan_error is not None:
            return ()
        nodes: list[WorkspaceTreeEntry] = []
        for child in observed:
            resolved_child = child.path.resolve()
            if child.is_dir:
                nodes.append(
                    DirectoryEntry(
                        path=child.path,
                        mtime_ns=child.mtime_ns,
                        git_status_flags=child.git_status_flags,
                        children=build_children(child.path) if resolved_child in expanded_resolved else (),
                    )
                )
            else:
                nodes.append(
                    FileEntry(
                        path=child.path,
                        file_size=child.file_size,
                        mtime_ns=child.mtime_ns,
                        git_status_flags=child.git_status_flags,
                        doc_summary=child.doc_summary,
                    )
                )
        return tuple(nodes)

    return directory_node(root, include_children=root in expanded_resolved)


# Kept as the concise construction verb used by the tree-row projection.
build_file_tree = build_workspace_tree
