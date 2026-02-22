"""Compatibility shim re-exporting fuzzy-search helpers.

The implementation moved to ``lazyviewer.search.fuzzy``.
This module preserves old import paths and patch points used by tests.
"""

from __future__ import annotations

from .search import fuzzy as _fuzzy
from .search.fuzzy import (
    STRICT_SUBSTRING_ONLY_MIN_FILES,
    clear_project_files_cache,
    collect_project_file_labels,
    collect_project_files,
    fuzzy_match_file_index,
    fuzzy_match_label_index,
    fuzzy_match_labels,
    fuzzy_match_paths,
    fuzzy_score,
    to_project_relative,
)

# Compatibility for tests/code patching `lazyviewer.fuzzy.shutil` and `subprocess`.
shutil = _fuzzy.shutil
subprocess = _fuzzy.subprocess

__all__ = [
    "STRICT_SUBSTRING_ONLY_MIN_FILES",
    "clear_project_files_cache",
    "collect_project_file_labels",
    "collect_project_files",
    "fuzzy_match_file_index",
    "fuzzy_match_label_index",
    "fuzzy_match_labels",
    "fuzzy_match_paths",
    "fuzzy_score",
    "to_project_relative",
]
