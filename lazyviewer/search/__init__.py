"""Typed file/content search service and pure fuzzy-matching helpers."""

from __future__ import annotations

from .content import ContentMatch, search_project_content_rg
from .fuzzy import (
    STRICT_SUBSTRING_ONLY_MIN_FILES,
    fuzzy_match_file_index,
    fuzzy_match_label_index,
    fuzzy_match_labels,
    fuzzy_match_paths,
    fuzzy_score,
    to_project_relative,
)
from .model import (
    ContentMatchesAdded,
    ContentSearchFinished,
    ContentSearchRequest,
    ContentSearchResult,
    FileSearchMatch,
    FileSearchRequest,
)
from .service import ContentSearchJob, SearchService

__all__ = [
    "ContentMatch",
    "STRICT_SUBSTRING_ONLY_MIN_FILES",
    "fuzzy_match_file_index",
    "fuzzy_match_label_index",
    "fuzzy_match_labels",
    "fuzzy_match_paths",
    "fuzzy_score",
    "search_project_content_rg",
    "to_project_relative",
    "ContentMatchesAdded",
    "ContentSearchFinished",
    "ContentSearchJob",
    "ContentSearchRequest",
    "ContentSearchResult",
    "FileSearchMatch",
    "FileSearchRequest",
    "SearchService",
]
