"""Snapshot helpers for file-tree domain state with fs/git change hooks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .fs import build_file_tree
from .types import DirectoryEntry
from .watch import build_git_watch_signature, build_tree_watch_signature


@dataclass(frozen=True)
class FileTreeSnapshot:
    """Snapshot of file-tree domain state plus last observed watch signatures."""

    root_path: Path
    expanded: frozenset[Path]
    show_hidden: bool
    skip_gitignored: bool
    tree_signature: str
    git_signature: str
    git_status_overlay: dict[Path, int]
    root_entry: DirectoryEntry


def _normalize_expanded(root: Path, expanded: set[Path]) -> frozenset[Path]:
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
    normalized.add(root)
    return frozenset(normalized)


def build_file_tree_snapshot(
    root: Path,
    expanded: set[Path],
    show_hidden: bool,
    *,
    skip_gitignored: bool = False,
    git_dir: Path | None = None,
    git_status_overlay: dict[Path, int] | None = None,
    collect_git_status_overlay: Callable[[Path], dict[Path, int]] | None = None,
    doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
    include_doc_summaries: bool = False,
) -> FileTreeSnapshot:
    """Build a fresh file-tree snapshot and capture fs/git signatures."""
    root = root.resolve()
    expanded_resolved = _normalize_expanded(root, expanded)

    if collect_git_status_overlay is not None:
        overlay = collect_git_status_overlay(root)
    else:
        overlay = git_status_overlay or {}

    root_entry = build_file_tree(
        root,
        set(expanded_resolved),
        show_hidden,
        skip_gitignored=skip_gitignored,
        git_status_overlay=overlay,
        doc_summary_for_path=doc_summary_for_path,
        include_doc_summaries=include_doc_summaries,
    )
    tree_signature = build_tree_watch_signature(root, set(expanded_resolved), show_hidden)
    git_signature = build_git_watch_signature(git_dir)
    return FileTreeSnapshot(
        root_path=root,
        expanded=expanded_resolved,
        show_hidden=show_hidden,
        skip_gitignored=skip_gitignored,
        tree_signature=tree_signature,
        git_signature=git_signature,
        git_status_overlay=overlay,
        root_entry=root_entry,
    )


def refresh_file_tree_snapshot(
    previous: FileTreeSnapshot,
    *,
    root: Path | None = None,
    expanded: set[Path] | None = None,
    show_hidden: bool | None = None,
    skip_gitignored: bool | None = None,
    git_dir: Path | None = None,
    git_status_overlay: dict[Path, int] | None = None,
    collect_git_status_overlay: Callable[[Path], dict[Path, int]] | None = None,
    doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
    include_doc_summaries: bool = False,
    force: bool = False,
) -> tuple[FileTreeSnapshot, bool, bool]:
    """Refresh snapshot when tree/git signatures or settings change.

    Returns ``(snapshot, tree_changed, git_changed)``.
    """
    root_path = root.resolve() if root is not None else previous.root_path
    expanded_resolved = _normalize_expanded(root_path, expanded if expanded is not None else set(previous.expanded))
    show_hidden_value = previous.show_hidden if show_hidden is None else show_hidden
    skip_gitignored_value = previous.skip_gitignored if skip_gitignored is None else skip_gitignored

    tree_signature = build_tree_watch_signature(root_path, set(expanded_resolved), show_hidden_value)
    git_signature = build_git_watch_signature(git_dir)

    config_changed = (
        root_path != previous.root_path
        or expanded_resolved != previous.expanded
        or show_hidden_value != previous.show_hidden
        or skip_gitignored_value != previous.skip_gitignored
    )
    tree_changed = force or config_changed or tree_signature != previous.tree_signature
    git_changed = force or config_changed or git_signature != previous.git_signature

    should_recompute_overlay = git_status_overlay is not None or collect_git_status_overlay is not None
    if should_recompute_overlay:
        if git_status_overlay is not None:
            overlay = git_status_overlay
        else:
            assert collect_git_status_overlay is not None
            overlay = collect_git_status_overlay(root_path)
    else:
        overlay = previous.git_status_overlay

    should_rebuild_tree = force or tree_changed or git_changed or should_recompute_overlay
    if not should_rebuild_tree:
        return previous, False, False

    root_entry = build_file_tree(
        root_path,
        set(expanded_resolved),
        show_hidden_value,
        skip_gitignored=skip_gitignored_value,
        git_status_overlay=overlay,
        doc_summary_for_path=doc_summary_for_path,
        include_doc_summaries=include_doc_summaries,
    )
    refreshed = FileTreeSnapshot(
        root_path=root_path,
        expanded=expanded_resolved,
        show_hidden=show_hidden_value,
        skip_gitignored=skip_gitignored_value,
        tree_signature=tree_signature,
        git_signature=git_signature,
        git_status_overlay=overlay,
        root_entry=root_entry,
    )
    return refreshed, tree_changed, git_changed


__all__ = [
    "FileTreeSnapshot",
    "build_file_tree_snapshot",
    "refresh_file_tree_snapshot",
]
