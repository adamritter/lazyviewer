"""Search package exports and compatibility aliases.

Combines content-search and fuzzy-index functionality in one import surface.
Also re-exports modules for existing tests that patch ``shutil``/``subprocess``.
"""

from __future__ import annotations

from . import content as _content
from . import fuzzy as _fuzzy
from .content import ContentMatch, search_project_content_rg
from .fuzzy import (
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

# Compatibility for tests/code patching `lazyviewer.search.shutil` and `subprocess`.
shutil = _content.shutil
subprocess = _content.subprocess

__all__ = [
    "ContentMatch",
    "STRICT_SUBSTRING_ONLY_MIN_FILES",
    "clear_project_files_cache",
    "collect_project_file_labels",
    "collect_project_files",
    "fuzzy_match_file_index",
    "fuzzy_match_label_index",
    "fuzzy_match_labels",
    "fuzzy_match_paths",
    "fuzzy_score",
    "search_project_content_rg",
    "to_project_relative",
]
