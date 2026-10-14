"""Domain model for filesystem file/directory trees plus update hooks.

This package contains non-UI tree primitives:
- file/directory entry datatypes with nested children
- filesystem scanning/build helpers
- watch-signature hooks for fs/git change detection
- snapshot refresh helpers keyed by watch signatures
"""

from __future__ import annotations

from .types import DirectoryEntry, FileEntry, FileTreeEntry
from .fs import (
    DirectoryChild,
    build_file_tree,
    list_directory_children,
    maybe_gitignore_matcher,
    safe_file_size,
    safe_mtime_ns,
)
from .watch import build_git_watch_signature, build_tree_watch_signature, resolve_git_paths
from .doc_summary import cached_top_file_doc_summary, clear_doc_summary_cache, top_file_doc_summary

def __getattr__(name):
    if name in {"FileTreeSnapshot", "build_file_tree_snapshot", "refresh_file_tree_snapshot"}:
        from . import snapshot
        return getattr(snapshot, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DirectoryEntry",
    "FileEntry",
    "FileTreeEntry",
    "DirectoryChild",
    "safe_file_size",
    "safe_mtime_ns",
    "maybe_gitignore_matcher",
    "list_directory_children",
    "build_file_tree",
    "FileTreeSnapshot",
    "build_file_tree_snapshot",
    "refresh_file_tree_snapshot",
    "build_tree_watch_signature",
    "build_git_watch_signature",
    "resolve_git_paths",
    "top_file_doc_summary",
    "cached_top_file_doc_summary",
    "clear_doc_summary_cache",
]
