"""Search package public exports."""

from __future__ import annotations

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
