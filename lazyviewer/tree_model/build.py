"""Tree-row projection built from ``file_tree_model`` domain entries."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ..file_tree_model.fs import (
    DirectoryChild,
    build_file_tree,
    list_directory_children,
    maybe_gitignore_matcher,
    safe_file_size,
    safe_mtime_ns,
)
from ..file_tree_model.types import DirectoryEntry, FileEntry
from .types import TreeEntry


def normalized_tree_roots(tree_roots: list[Path], active_root: Path) -> list[Path]:
    """Return resolved workspace roots preserving duplicates; include active if absent."""
    normalized = [raw_root.resolve() for raw_root in tree_roots]
    resolved_active = active_root.resolve()
    if not any(root == resolved_active for root in normalized):
        normalized.append(resolved_active)
    return normalized


def build_tree_entries(
    root: Path,
    expanded: set[Path],
    show_hidden: bool,
    skip_gitignored: bool = False,
    git_status_overlay: dict[Path, int] | None = None,
    doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
    include_doc_summaries: bool = False,
    workspace_root: Path | None = None,
    workspace_section: int | None = None,
) -> list[TreeEntry]:
    """Build full tree-entry list rooted at ``root`` honoring expansion state."""
    domain_root = build_file_tree(
        root,
        expanded,
        show_hidden,
        skip_gitignored=skip_gitignored,
        git_status_overlay=git_status_overlay,
        doc_summary_for_path=doc_summary_for_path,
        include_doc_summaries=include_doc_summaries,
    )

    entries: list[TreeEntry] = []
    section_root = (workspace_root or root).resolve()

    def append_rows(node: DirectoryEntry | FileEntry, depth: int) -> None:
        """Append one domain node and any nested children as flat tree rows."""
        if isinstance(node, DirectoryEntry):
            entries.append(
                TreeEntry(
                    node.path,
                    depth,
                    True,
                    mtime_ns=node.mtime_ns,
                    git_status_flags=node.git_status_flags,
                    workspace_root=section_root,
                    workspace_section=workspace_section,
                )
            )
            for child in node.children:
                append_rows(child, depth + 1)
            return

        entries.append(
            TreeEntry(
                node.path,
                depth,
                False,
                file_size=node.file_size,
                mtime_ns=node.mtime_ns,
                git_status_flags=node.git_status_flags,
                doc_summary=node.doc_summary,
                workspace_root=section_root,
                workspace_section=workspace_section,
            )
        )

    append_rows(domain_root, 0)
    return entries


def build_workspace_tree_entries(
    tree_roots: list[Path],
    active_root: Path,
    expanded: set[Path],
    expanded_by_root: list[set[Path]] | None,
    show_hidden: bool,
    skip_gitignored: bool = False,
    git_status_overlay: dict[Path, int] | None = None,
    doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
    include_doc_summaries: bool = False,
) -> list[TreeEntry]:
    """Build one flat tree containing all configured workspace roots."""
    roots = normalized_tree_roots(tree_roots, active_root)
    entries: list[TreeEntry] = []
    for section_idx, root in enumerate(roots):
        expanded_for_root = (
            set(expanded_by_root[section_idx])
            if expanded_by_root is not None and section_idx < len(expanded_by_root)
            else set(expanded)
        )
        section_entries = build_tree_entries(
            root,
            expanded_for_root,
            show_hidden,
            skip_gitignored=skip_gitignored,
            git_status_overlay=git_status_overlay,
            doc_summary_for_path=doc_summary_for_path,
            include_doc_summaries=include_doc_summaries,
            workspace_root=root,
            workspace_section=section_idx,
        )
        entries.extend(section_entries)
    return entries
