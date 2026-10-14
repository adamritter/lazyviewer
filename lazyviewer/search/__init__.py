"""Search package exports for fuzzy file matching and content search.

The public surface intentionally exposes both cached file-index helpers and the
ripgrep-backed content-search entry point used by content filter mode.
"""

from __future__ import annotations

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

def __getattr__(name):
    if name in {"ContentMatch", "search_project_content_rg"}:
        from . import content
        return getattr(content, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
