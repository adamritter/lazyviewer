"""Matching, content-search caching, and tree rebuild logic."""

from __future__ import annotations

import time
from collections import OrderedDict
from pathlib import Path

from ...search.content import ContentMatch, search_project_content_rg
from ...search.fuzzy import (
    STRICT_SUBSTRING_ONLY_MIN_FILES,
    collect_project_file_labels,
    fuzzy_match_label_index,
)
from ..model import (
    build_tree_entries,
    filter_tree_entries_for_content_matches,
    filter_tree_entries_for_files,
    find_content_hit_index,
)
from .helpers import skip_gitignored_for_hidden_mode
from .limits import (
    CONTENT_SEARCH_CACHE_MAX_QUERIES,
    CONTENT_SEARCH_FILE_LIMIT,
    content_search_match_limit_for_query,
    tree_filter_match_limit_for_query,
)


class TreeFilterMatchingMixin:
    """Query matching, cacheing, and tree-entry rebuild methods."""

    def refresh_tree_filter_file_index(self) -> None:
        """Refresh cached file-label index when root/hidden-mode changes."""
        root = self.state.tree_root.resolve()
        if self.state.picker_files_root == root and self.state.picker_files_show_hidden == self.state.show_hidden:
            return
        self.state.picker_file_labels = collect_project_file_labels(
            root,
            self.state.show_hidden,
            skip_gitignored=skip_gitignored_for_hidden_mode(self.state.show_hidden),
        )
        self.state.picker_file_labels_folded = []
        self.state.picker_files_root = root
        self.state.picker_files_show_hidden = self.state.show_hidden

    def default_selected_index(self, prefer_files: bool = False) -> int:
        """Return default selected tree index after (re)building entries."""
        if not self.state.tree_entries:
            return 0
        if prefer_files:
            for idx, entry in enumerate(self.state.tree_entries):
                if not entry.is_dir:
                    return idx
        if len(self.state.tree_entries) > 1:
            return 1
        return 0

    def tree_filter_match_limit(self, query: str) -> int:
        """Return adaptive file-filter match cap based on query length."""
        return tree_filter_match_limit_for_query(query)

    def content_search_match_limit(self, query: str) -> int:
        """Return adaptive content-search match cap based on query length."""
        return content_search_match_limit_for_query(query)

    def content_search_cache_key(self, query: str, max_matches: int) -> tuple[str, str, bool, bool, int, int]:
        """Build stable cache key for one content-search request."""
        skip_gitignored = skip_gitignored_for_hidden_mode(self.state.show_hidden)
        return (
            str(self.state.tree_root.resolve()),
            query,
            self.state.show_hidden,
            skip_gitignored,
            max(1, max_matches),
            CONTENT_SEARCH_FILE_LIMIT,
        )

    def search_project_content_cached(
        self,
        query: str,
        max_matches: int,
    ) -> tuple[dict[Path, list[ContentMatch]], bool, str | None]:
        """Run content search with LRU caching by query/root/mode/limits."""
        key = self.content_search_cache_key(query, max_matches)
        cached = self.content_search_cache.get(key)
        if cached is not None:
            self.content_search_cache.move_to_end(key)
            return cached

        result = search_project_content_rg(
            self.state.tree_root,
            query,
            self.state.show_hidden,
            skip_gitignored=skip_gitignored_for_hidden_mode(self.state.show_hidden),
            max_matches=max(1, max_matches),
            max_files=CONTENT_SEARCH_FILE_LIMIT,
        )
        self.content_search_cache[key] = result
        self.content_search_cache.move_to_end(key)
        while len(self.content_search_cache) > CONTENT_SEARCH_CACHE_MAX_QUERIES:
            self.content_search_cache.popitem(last=False)
        return result

    def rebuild_tree_entries(
        self,
        preferred_path: Path | None = None,
        center_selection: bool = False,
        force_first_file: bool = False,
    ) -> None:
        """Rebuild tree entries for current filter state and preserve intent.

        Preserves current file/hit when possible, otherwise picks nearest valid
        result depending on mode and query state.
        """
        previous_selected_hit_path: Path | None = None
        previous_selected_hit_line: int | None = None
        previous_selected_hit_column: int | None = None
        if self.state.tree_entries and 0 <= self.state.selected_idx < len(self.state.tree_entries):
            previous_entry = self.state.tree_entries[self.state.selected_idx]
            if previous_entry.kind == "search_hit":
                previous_selected_hit_path = previous_entry.path.resolve()
                previous_selected_hit_line = previous_entry.line
                previous_selected_hit_column = previous_entry.column

        if preferred_path is None:
            if self.state.tree_entries and 0 <= self.state.selected_idx < len(self.state.tree_entries):
                preferred_path = self.state.tree_entries[self.state.selected_idx].path.resolve()
            else:
                preferred_path = self.state.current_path.resolve()

        if self.state.tree_filter_active and self.state.tree_filter_query:
            if self.state.tree_filter_mode == "content":
                match_limit = self.content_search_match_limit(self.state.tree_filter_query)
                matches_by_file, truncated, _error = self.search_project_content_cached(
                    self.state.tree_filter_query,
                    match_limit,
                )
                self.state.tree_filter_match_count = sum(len(items) for items in matches_by_file.values())
                self.state.tree_filter_truncated = truncated
                self.state.tree_entries, self.state.tree_render_expanded = filter_tree_entries_for_content_matches(
                    self.state.tree_root,
                    self.state.expanded,
                    matches_by_file,
                    collapsed_dirs=self.state.tree_filter_collapsed_dirs,
                )
            else:
                self.refresh_tree_filter_file_index()
                match_limit = min(len(self.state.picker_file_labels), self.tree_filter_match_limit(self.state.tree_filter_query))
                labels_folded: list[str] | None = None
                if len(self.state.picker_file_labels) < STRICT_SUBSTRING_ONLY_MIN_FILES:
                    if len(self.state.picker_file_labels_folded) != len(self.state.picker_file_labels):
                        self.state.picker_file_labels_folded = [label.casefold() for label in self.state.picker_file_labels]
                    labels_folded = self.state.picker_file_labels_folded
                raw_matched = fuzzy_match_label_index(
                    self.state.tree_filter_query,
                    self.state.picker_file_labels,
                    labels_folded=labels_folded,
                    limit=max(1, match_limit + 1),
                )
                self.state.tree_filter_truncated = len(raw_matched) > match_limit
                matched = raw_matched[:match_limit] if match_limit > 0 else []
                root = self.state.tree_root.resolve()
                matched_paths = [root / label for _, label, _ in matched]
                self.state.tree_filter_match_count = len(matched_paths)
                self.state.tree_entries, self.state.tree_render_expanded = filter_tree_entries_for_files(
                    self.state.tree_root,
                    self.state.expanded,
                    self.state.show_hidden,
                    matched_paths,
                    skip_gitignored=skip_gitignored_for_hidden_mode(self.state.show_hidden),
                )
        else:
            self.state.tree_filter_match_count = 0
            self.state.tree_filter_truncated = False
            self.state.tree_render_expanded = set(self.state.expanded)
            self.state.tree_entries = build_tree_entries(
                self.state.tree_root,
                self.state.expanded,
                self.state.show_hidden,
                skip_gitignored=skip_gitignored_for_hidden_mode(self.state.show_hidden),
            )

        if force_first_file:
            first_idx = self.next_tree_filter_result_entry_index(-1, 1)
            self.state.selected_idx = first_idx if first_idx is not None else 0
        else:
            preferred_target = preferred_path.resolve()
            self.state.selected_idx = 0
            matched_preferred = False
            if (
                self.state.tree_filter_active
                and self.state.tree_filter_query
                and self.state.tree_filter_mode == "content"
                and previous_selected_hit_path is not None
            ):
                preserved_hit_idx = find_content_hit_index(
                    self.state.tree_entries,
                    previous_selected_hit_path,
                    preferred_line=previous_selected_hit_line,
                    preferred_column=previous_selected_hit_column,
                )
                if preserved_hit_idx is not None:
                    self.state.selected_idx = preserved_hit_idx
                    matched_preferred = True

            if not matched_preferred:
                for idx, entry in enumerate(self.state.tree_entries):
                    if entry.kind == "search_hit":
                        continue
                    if entry.path.resolve() == preferred_target:
                        self.state.selected_idx = idx
                        matched_preferred = True
                        break

            if not matched_preferred:
                if self.state.tree_filter_active and self.state.tree_filter_query:
                    first_idx = self.next_tree_filter_result_entry_index(-1, 1)
                    self.state.selected_idx = first_idx if first_idx is not None else 0
                else:
                    self.state.selected_idx = self.default_selected_index(prefer_files=bool(self.state.tree_filter_query))

            if (
                self.state.tree_filter_active
                and self.state.tree_filter_query
                and self.state.tree_filter_mode == "content"
                and not self.state.tree_filter_editing
            ):
                coerced_idx = self.coerce_tree_filter_result_index(self.state.selected_idx)
                self.state.selected_idx = coerced_idx if coerced_idx is not None else 0

        if center_selection:
            rows = self.tree_view_rows()
            self.state.tree_start = max(0, self.state.selected_idx - max(1, rows // 2))

    def apply_tree_filter_query(
        self,
        query: str,
        preview_selection: bool = False,
        select_first_file: bool = False,
    ) -> None:
        """Apply query text, rebuild results, and update loading indicator timing."""
        self.state.tree_filter_query = query
        if not query:
            self.loading_until = 0.0
        else:
            needs_loading_indicator = True
            if self.state.tree_filter_mode == "content":
                match_limit = self.content_search_match_limit(query)
                cache_key = self.content_search_cache_key(query, match_limit)
                needs_loading_indicator = cache_key not in self.content_search_cache
            if needs_loading_indicator:
                self.loading_until = time.monotonic() + 0.35
            else:
                self.loading_until = 0.0
        force_first_file = select_first_file and bool(query)
        preferred_path = None if force_first_file else self.state.current_path.resolve()
        self.rebuild_tree_entries(
            preferred_path=preferred_path,
            force_first_file=force_first_file,
        )
        if preview_selection:
            self.preview_selected_entry(force=True)
        self.state.dirty = True
        if self.on_tree_filter_state_change is not None:
            self.on_tree_filter_state_change()

    def init_content_search_cache(self) -> None:
        """Initialize search cache storage."""
        self.content_search_cache: OrderedDict[
            tuple[str, str, bool, bool, int, int],
            tuple[dict[Path, list[ContentMatch]], bool, str | None],
        ] = OrderedDict()
