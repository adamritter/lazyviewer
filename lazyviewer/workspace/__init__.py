"""Public workspace component API."""

from .model import (
    WorkspaceDelta,
    WorkspaceFileIndex,
    WorkspaceQuery,
    WorkspaceRevision,
    WorkspaceRootSnapshot,
    WorkspaceSnapshot,
)
from .index import WorkspaceIndexWarmup, build_workspace_file_index, collect_root_file_labels
from .service import WorkspaceService
from .watch import WorkspaceWatcher
from .doc_summary import cached_top_file_doc_summary, clear_doc_summary_cache, top_file_doc_summary
from .signatures import build_git_watch_signature, build_tree_watch_signature, resolve_git_paths
from .tree import (
    DirectoryChild,
    DirectoryEntry,
    FileEntry,
    WorkspaceTreeEntry,
    build_file_tree,
    build_workspace_tree,
    list_directory_children,
    maybe_gitignore_matcher,
    safe_file_size,
    safe_mtime_ns,
)

__all__ = [
    "WorkspaceDelta",
    "WorkspaceFileIndex",
    "WorkspaceIndexWarmup",
    "build_workspace_file_index",
    "collect_root_file_labels",
    "WorkspaceQuery",
    "WorkspaceRevision",
    "WorkspaceRootSnapshot",
    "WorkspaceService",
    "WorkspaceSnapshot",
    "WorkspaceWatcher",
    "WorkspaceTreeEntry",
    "DirectoryChild",
    "DirectoryEntry",
    "FileEntry",
    "build_file_tree",
    "build_workspace_tree",
    "list_directory_children",
    "maybe_gitignore_matcher",
    "safe_file_size",
    "safe_mtime_ns",
    "build_git_watch_signature",
    "build_tree_watch_signature",
    "resolve_git_paths",
    "cached_top_file_doc_summary",
    "clear_doc_summary_cache",
    "top_file_doc_summary",
]
