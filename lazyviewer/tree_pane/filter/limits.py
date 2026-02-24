"""Query-adaptive limits for tree filtering and content search."""

from __future__ import annotations

TREE_FILTER_MATCH_LIMIT_1CHAR = 300
TREE_FILTER_MATCH_LIMIT_2CHAR = 1_000
TREE_FILTER_MATCH_LIMIT_3CHAR = 3_000
TREE_FILTER_MATCH_LIMIT_DEFAULT = 8_000
CONTENT_SEARCH_MATCH_LIMIT_1CHAR = 300
CONTENT_SEARCH_MATCH_LIMIT_2CHAR = 1_000
CONTENT_SEARCH_MATCH_LIMIT_3CHAR = 2_000
CONTENT_SEARCH_MATCH_LIMIT_DEFAULT = 4_000
CONTENT_SEARCH_FILE_LIMIT = 800
CONTENT_SEARCH_CACHE_MAX_QUERIES = 64


def tree_filter_match_limit_for_query(query: str) -> int:
    """Return adaptive file-filter match cap based on query length."""
    if len(query) <= 1:
        return TREE_FILTER_MATCH_LIMIT_1CHAR
    if len(query) == 2:
        return TREE_FILTER_MATCH_LIMIT_2CHAR
    if len(query) == 3:
        return TREE_FILTER_MATCH_LIMIT_3CHAR
    return TREE_FILTER_MATCH_LIMIT_DEFAULT


def content_search_match_limit_for_query(query: str) -> int:
    """Return adaptive content-search match cap based on query length."""
    if len(query) <= 1:
        return CONTENT_SEARCH_MATCH_LIMIT_1CHAR
    if len(query) == 2:
        return CONTENT_SEARCH_MATCH_LIMIT_2CHAR
    if len(query) == 3:
        return CONTENT_SEARCH_MATCH_LIMIT_3CHAR
    return CONTENT_SEARCH_MATCH_LIMIT_DEFAULT
