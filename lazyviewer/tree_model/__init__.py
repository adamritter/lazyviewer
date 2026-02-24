"""Tree-model creation, filtering, navigation, and row formatting.

Defines ``TreeEntry`` and helpers for directory/file projection views.
Also formats rows with search-hit text and git status badges.
"""

from __future__ import annotations

from .build import DirectoryChild, build_tree_entries, list_directory_children, maybe_gitignore_matcher
from .doc_summary import clear_doc_summary_cache
from .filtering import (
    filter_tree_entries_for_content_matches,
    filter_tree_entries_for_files,
    find_content_hit_index,
)
from .layout import clamp_left_width, compute_left_width
from .navigation import (
    next_directory_entry_index,
    next_file_entry_index,
    next_index_after_directory_subtree,
    next_opened_directory_entry_index,
)
from .rendering import file_color_for, format_tree_entry
from .types import TreeEntry

__all__ = [
    "TreeEntry",
    "DirectoryChild",
    "build_tree_entries",
    "list_directory_children",
    "maybe_gitignore_matcher",
    "clear_doc_summary_cache",
    "filter_tree_entries_for_content_matches",
    "filter_tree_entries_for_files",
    "find_content_hit_index",
    "next_file_entry_index",
    "next_directory_entry_index",
    "next_opened_directory_entry_index",
    "next_index_after_directory_subtree",
    "format_tree_entry",
    "file_color_for",
    "compute_left_width",
    "clamp_left_width",
]
