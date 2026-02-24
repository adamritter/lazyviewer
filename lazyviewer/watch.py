"""Compatibility wrapper for file-tree watch signature helpers."""

from __future__ import annotations

from .file_tree_model.watch import (
    build_git_watch_signature,
    build_tree_watch_signature,
    resolve_git_paths,
)

__all__ = [
    "build_tree_watch_signature",
    "build_git_watch_signature",
    "resolve_git_paths",
]
