"""Tree-entry construction and directory child metadata helpers."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..gitignore import get_gitignore_matcher
from .doc_summary import cached_top_file_doc_summary
from .types import TreeEntry


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


@dataclass(frozen=True)
class DirectoryChild:
    """One directory-child record with metadata used by UI renderers."""

    name: str
    path: Path
    is_dir: bool
    file_size: int | None
    mtime_ns: int | None
    git_status_flags: int = 0
    doc_summary: str | None = None


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


def build_tree_entries(
    root: Path,
    expanded: set[Path],
    show_hidden: bool,
    skip_gitignored: bool = False,
    git_status_overlay: dict[Path, int] | None = None,
    doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
    include_doc_summaries: bool = False,
) -> list[TreeEntry]:
    """Build full tree-entry list rooted at ``root`` honoring expansion state."""
    root = root.resolve()
    entries: list[TreeEntry] = [
        TreeEntry(
            root,
            0,
            True,
            mtime_ns=safe_mtime_ns(root),
            git_status_flags=int(git_status_overlay.get(root, 0)) if git_status_overlay is not None else 0,
        )
    ]
    ignore_matcher = maybe_gitignore_matcher(root, skip_gitignored)

    def walk(directory: Path, depth: int) -> None:
        """Depth-first traversal adding visible children for expanded directories."""
        children, scan_error, _directory_mtime_ns = list_directory_children(
            directory,
            show_hidden,
            ignore_matcher=ignore_matcher,
            git_status_overlay=git_status_overlay,
            doc_summary_for_path=doc_summary_for_path,
            include_doc_summaries=include_doc_summaries,
        )
        if scan_error is not None:
            return
        for child in children:
            entries.append(
                TreeEntry(
                    child.path,
                    depth,
                    child.is_dir,
                    file_size=child.file_size,
                    mtime_ns=child.mtime_ns,
                    git_status_flags=child.git_status_flags,
                    doc_summary=child.doc_summary,
                )
            )
            if child.is_dir and child.path.resolve() in expanded:
                walk(child.path, depth + 1)

    if root in expanded:
        walk(root, 1)
    return entries
