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
            )
        )

    append_rows(domain_root, 0)
    return entries
