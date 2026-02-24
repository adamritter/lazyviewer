"""Filesystem scanning and domain-tree construction for file/directory models."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..gitignore import get_gitignore_matcher
from .doc_summary import cached_top_file_doc_summary
from .types import DirectoryEntry, FileEntry


@dataclass(frozen=True)
class DirectoryChild:
    """One visible directory child row plus cached metadata."""

    name: str
    path: Path
    is_dir: bool
    file_size: int | None
    mtime_ns: int | None
    git_status_flags: int = 0
    doc_summary: str | None = None


def safe_file_size(path: Path, is_dir: bool) -> int | None:
    """Return file size for files, otherwise ``None`` or on stat failure."""
    if is_dir:
        return None
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


def safe_mtime_ns(path: Path) -> int | None:
    """Return ``st_mtime_ns`` for ``path`` or ``None`` on stat failure."""
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return None


def maybe_gitignore_matcher(root: Path, skip_gitignored: bool) -> object | None:
    """Return a gitignore matcher rooted at ``root`` when enabled."""
    if not skip_gitignored:
        return None
    return get_gitignore_matcher(root)


def list_directory_children(
    directory: Path,
    show_hidden: bool,
    ignore_matcher: object | None = None,
    git_status_overlay: dict[Path, int] | None = None,
    doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
    include_doc_summaries: bool = False,
) -> tuple[list[DirectoryChild], Exception | None, int | None]:
    """List visible children with stat/git/doc metadata and sorted order.

    Returns ``(children, scan_error, directory_mtime_ns)``. ``scan_error`` is
    set when the directory cannot be scanned.
    """
    try:
        resolved_directory = directory.resolve()
    except Exception:
        resolved_directory = directory

    directory_mtime_ns = safe_mtime_ns(resolved_directory)
    children: list[DirectoryChild] = []
    summary_provider = doc_summary_for_path
    if summary_provider is None and include_doc_summaries:
        summary_provider = cached_top_file_doc_summary

    try:
        with os.scandir(directory) as entries:
            for child in entries:
                name = child.name
                if not show_hidden and name.startswith("."):
                    continue
                child_path = Path(child.path)
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

                if git_status_overlay is not None:
                    try:
                        resolved_child = child_path.resolve()
                    except Exception:
                        resolved_child = child_path
                    git_status_flags = int(
                        git_status_overlay.get(
                            resolved_child,
                            git_status_overlay.get(child_path, 0),
                        )
                    )
                else:
                    git_status_flags = 0

                doc_summary: str | None = None
                if not is_dir and summary_provider is not None:
                    try:
                        doc_summary = summary_provider(child_path, file_size)
                    except Exception:
                        doc_summary = None

                children.append(
                    DirectoryChild(
                        name=name,
                        path=child_path,
                        is_dir=is_dir,
                        file_size=file_size,
                        mtime_ns=mtime_ns,
                        git_status_flags=git_status_flags,
                        doc_summary=doc_summary,
                    )
                )
    except (PermissionError, OSError) as exc:
        return [], exc, directory_mtime_ns

    children.sort(key=lambda item: (not item.is_dir, item.name.lower()))
    return children, None, directory_mtime_ns


def _normalize_expanded(root: Path, expanded: set[Path]) -> set[Path]:
    """Normalize expanded paths and keep only entries under ``root``."""
    normalized: set[Path] = set()
    for raw_path in expanded:
        try:
            resolved = raw_path.resolve()
        except Exception:
            continue
        if not resolved.is_relative_to(root):
            continue
        normalized.add(resolved)
    return normalized


def build_file_tree(
    root: Path,
    expanded: set[Path],
    show_hidden: bool,
    skip_gitignored: bool = False,
    git_status_overlay: dict[Path, int] | None = None,
    doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
    include_doc_summaries: bool = False,
) -> DirectoryEntry:
    """Build a domain file-tree rooted at ``root`` honoring expansion state."""
    root = root.resolve()
    expanded_resolved = _normalize_expanded(root, expanded)
    root_entry = DirectoryEntry(
        path=root,
        mtime_ns=safe_mtime_ns(root),
        git_status_flags=int(git_status_overlay.get(root, 0)) if git_status_overlay is not None else 0,
        children=(),
    )
    ignore_matcher = maybe_gitignore_matcher(root, skip_gitignored)

    def build_children(directory: Path) -> tuple[DirectoryEntry | FileEntry, ...]:
        children, scan_error, _directory_mtime_ns = list_directory_children(
            directory,
            show_hidden,
            ignore_matcher=ignore_matcher,
            git_status_overlay=git_status_overlay,
            doc_summary_for_path=doc_summary_for_path,
            include_doc_summaries=include_doc_summaries,
        )
        if scan_error is not None:
            return ()

        nodes: list[DirectoryEntry | FileEntry] = []
        for child in children:
            if child.is_dir:
                child_entry = DirectoryEntry(
                    path=child.path,
                    mtime_ns=child.mtime_ns,
                    git_status_flags=child.git_status_flags,
                    children=(),
                )
                try:
                    resolved_child = child.path.resolve()
                except Exception:
                    resolved_child = child.path
                if resolved_child in expanded_resolved:
                    child_entry = DirectoryEntry(
                        path=child.path,
                        mtime_ns=child.mtime_ns,
                        git_status_flags=child.git_status_flags,
                        children=build_children(child.path),
                    )
                nodes.append(child_entry)
                continue

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

    if root in expanded_resolved:
        root_entry = DirectoryEntry(
            path=root_entry.path,
            mtime_ns=root_entry.mtime_ns,
            git_status_flags=root_entry.git_status_flags,
            children=build_children(root),
        )
    return root_entry


__all__ = [
    "DirectoryChild",
    "safe_file_size",
    "safe_mtime_ns",
    "maybe_gitignore_matcher",
    "list_directory_children",
    "build_file_tree",
]
