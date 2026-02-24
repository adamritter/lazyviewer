"""Controller for file-filter and content-search modes in the tree pane.

This module owns filter session state transitions, query application, result
tree rebuilding, and selection coercion. It keeps UI handlers thin by exposing
one stateful operations object (`TreeFilterOps`) that encapsulates both mode
semantics and performance behavior (match limits + cached content-search calls).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..navigation import JumpLocation
from ..search.content import ContentMatch, search_project_content_rg
from ..search.fuzzy import (
    STRICT_SUBSTRING_ONLY_MIN_FILES,
    collect_project_file_labels,
    fuzzy_match_label_index,
)
from ..runtime.state import AppState
from ..tree_pane.model import (
    build_tree_entries,
    filter_tree_entries_for_content_matches,
    filter_tree_entries_for_files,
    find_content_hit_index,
    next_file_entry_index,
)

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


def _skip_gitignored_for_hidden_mode(show_hidden: bool) -> bool:
    """Return whether gitignored paths should be excluded for current hidden mode."""
    # Hidden mode should reveal both dotfiles and gitignored paths.
    return not show_hidden


@dataclass(frozen=True)
class TreeFilterDeps:
    """Runtime dependencies required by :class:`TreeFilterOps`."""

    state: AppState
    visible_content_rows: Callable[[], int]
    rebuild_screen_lines: Callable[..., None]
    preview_selected_entry: Callable[..., None]
    current_jump_location: Callable[[], JumpLocation]
    record_jump_if_changed: Callable[[JumpLocation], None]
    jump_to_path: Callable[[Path], None]
    jump_to_line: Callable[[int], None]
    on_tree_filter_state_change: Callable[[], None] | None = None


class TreeFilterOps:
    """Stateful controller for tree filter lifecycle and navigation."""

    def __init__(self, deps: TreeFilterDeps) -> None:
        """Create operations object bound to shared runtime state."""
        self.state = deps.state
        self.visible_content_rows = deps.visible_content_rows
        self.rebuild_screen_lines = deps.rebuild_screen_lines
        self.preview_selected_entry = deps.preview_selected_entry
        self.current_jump_location = deps.current_jump_location
        self.record_jump_if_changed = deps.record_jump_if_changed
        self.jump_to_path = deps.jump_to_path
        self.jump_to_line = deps.jump_to_line
        self.on_tree_filter_state_change = deps.on_tree_filter_state_change
        self.loading_until = 0.0
        self.content_search_cache: OrderedDict[
            tuple[str, str, bool, bool, int, int],
            tuple[dict[Path, list[ContentMatch]], bool, str | None],
        ] = OrderedDict()

    def get_loading_until(self) -> float:
        """Return timestamp until which loading indicator should remain visible."""
        return self.loading_until

    def refresh_tree_filter_file_index(self) -> None:
        """Refresh cached file-label index when root/hidden-mode changes."""
        root = self.state.tree_root.resolve()
        if self.state.picker_files_root == root and self.state.picker_files_show_hidden == self.state.show_hidden:
            return
        self.state.picker_file_labels = collect_project_file_labels(
            root,
            self.state.show_hidden,
            skip_gitignored=_skip_gitignored_for_hidden_mode(self.state.show_hidden),
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

    def tree_filter_prompt_prefix(self) -> str:
        """Return prompt prefix for active filter mode."""
        return "/>" if self.state.tree_filter_mode == "content" else "p>"

    def tree_filter_placeholder(self) -> str:
        """Return placeholder text for active filter mode."""
        return "type to search content" if self.state.tree_filter_mode == "content" else "type to filter files"

    def tree_view_rows(self) -> int:
        """Return visible tree rows, reserving one row for active filter prompt."""
        rows = self.visible_content_rows()
        if self.state.tree_filter_active and not self.state.picker_active:
            return max(1, rows - 1)
        return rows

    def tree_filter_match_limit(self, query: str) -> int:
        """Return adaptive file-filter match cap based on query length."""
        if len(query) <= 1:
            return TREE_FILTER_MATCH_LIMIT_1CHAR
        if len(query) == 2:
            return TREE_FILTER_MATCH_LIMIT_2CHAR
        if len(query) == 3:
            return TREE_FILTER_MATCH_LIMIT_3CHAR
        return TREE_FILTER_MATCH_LIMIT_DEFAULT

    def content_search_match_limit(self, query: str) -> int:
        """Return adaptive content-search match cap based on query length."""
        if len(query) <= 1:
            return CONTENT_SEARCH_MATCH_LIMIT_1CHAR
        if len(query) == 2:
            return CONTENT_SEARCH_MATCH_LIMIT_2CHAR
        if len(query) == 3:
            return CONTENT_SEARCH_MATCH_LIMIT_3CHAR
        return CONTENT_SEARCH_MATCH_LIMIT_DEFAULT

    def _content_search_cache_key(self, query: str, max_matches: int) -> tuple[str, str, bool, bool, int, int]:
        """Build stable cache key for one content-search request."""
        skip_gitignored = _skip_gitignored_for_hidden_mode(self.state.show_hidden)
        return (
            str(self.state.tree_root.resolve()),
            query,
            self.state.show_hidden,
            skip_gitignored,
            max(1, max_matches),
            CONTENT_SEARCH_FILE_LIMIT,
        )

    def _search_project_content_cached(
        self,
        query: str,
        max_matches: int,
    ) -> tuple[dict[Path, list[ContentMatch]], bool, str | None]:
        """Run content search with LRU caching by query/root/mode/limits."""
        key = self._content_search_cache_key(query, max_matches)
        cached = self.content_search_cache.get(key)
        if cached is not None:
            self.content_search_cache.move_to_end(key)
            return cached

        result = search_project_content_rg(
            self.state.tree_root,
            query,
            self.state.show_hidden,
            skip_gitignored=_skip_gitignored_for_hidden_mode(self.state.show_hidden),
            max_matches=max(1, max_matches),
            max_files=CONTENT_SEARCH_FILE_LIMIT,
        )
        self.content_search_cache[key] = result
        self.content_search_cache.move_to_end(key)
        while len(self.content_search_cache) > CONTENT_SEARCH_CACHE_MAX_QUERIES:
            self.content_search_cache.popitem(last=False)
        return result

    def next_content_hit_entry_index(self, selected_idx: int, direction: int) -> int | None:
        """Return next search-hit entry index from selected index."""
        if not self.state.tree_entries or direction == 0:
            return None
        step = 1 if direction > 0 else -1
        idx = selected_idx + step
        while 0 <= idx < len(self.state.tree_entries):
            if self.state.tree_entries[idx].kind == "search_hit":
                return idx
            idx += step
        return None

    def next_tree_filter_result_entry_index(self, selected_idx: int, direction: int) -> int | None:
        """Return next result row index for active filter mode."""
        if self.state.tree_filter_mode == "content":
            return self.next_content_hit_entry_index(selected_idx, direction)
        return next_file_entry_index(self.state.tree_entries, selected_idx, direction)

    def nearest_tree_filter_result_entry_index(self, selected_idx: int) -> int | None:
        """Return closest result row index around selected index."""
        candidate_idx = self.next_tree_filter_result_entry_index(selected_idx, 1)
        if candidate_idx is None:
            candidate_idx = self.next_tree_filter_result_entry_index(selected_idx, -1)
        return candidate_idx

    def coerce_tree_filter_result_index(self, idx: int) -> int | None:
        """Coerce arbitrary row index onto nearest selectable filter result."""
        if not (0 <= idx < len(self.state.tree_entries)):
            return None
        if not (self.state.tree_filter_active and self.state.tree_filter_query):
            return idx

        entry = self.state.tree_entries[idx]
        if self.state.tree_filter_mode == "content":
            if entry.kind == "search_hit":
                return idx
        elif not entry.is_dir:
            return idx

        return self.nearest_tree_filter_result_entry_index(idx)

    def move_tree_selection(self, direction: int) -> bool:
        """Move tree selection, honoring filter-result-only navigation when active."""
        if not self.state.tree_entries or direction == 0:
            return False

        if self.state.tree_filter_active and self.state.tree_filter_query:
            target_idx = self.next_tree_filter_result_entry_index(self.state.selected_idx, direction)
            if target_idx is None:
                return False
        else:
            step = 1 if direction > 0 else -1
            target_idx = max(0, min(len(self.state.tree_entries) - 1, self.state.selected_idx + step))

        if target_idx == self.state.selected_idx:
            return False

        self.state.selected_idx = target_idx
        self.preview_selected_entry()
        return True

    def jump_to_next_content_hit(self, direction: int) -> bool:
        """Jump to next/previous content hit with wrap-around behavior."""
        if direction == 0:
            return False
        if direction > 0:
            target_idx = self.next_content_hit_entry_index(self.state.selected_idx, 1)
            if target_idx is None:
                target_idx = self.next_content_hit_entry_index(-1, 1)
        else:
            target_idx = self.next_content_hit_entry_index(self.state.selected_idx, -1)
            if target_idx is None:
                target_idx = self.next_content_hit_entry_index(len(self.state.tree_entries), -1)

        if target_idx is None or target_idx == self.state.selected_idx:
            return False

        origin = self.current_jump_location()
        self.state.selected_idx = target_idx
        self.preview_selected_entry()
        self.record_jump_if_changed(origin)
        return True

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
                matches_by_file, truncated, _error = self._search_project_content_cached(
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
                    skip_gitignored=_skip_gitignored_for_hidden_mode(self.state.show_hidden),
                )
        else:
            self.state.tree_filter_match_count = 0
            self.state.tree_filter_truncated = False
            self.state.tree_render_expanded = set(self.state.expanded)
            self.state.tree_entries = build_tree_entries(
                self.state.tree_root,
                self.state.expanded,
                self.state.show_hidden,
                skip_gitignored=_skip_gitignored_for_hidden_mode(self.state.show_hidden),
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
                cache_key = self._content_search_cache_key(query, match_limit)
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

    def reset_tree_filter_session_state(self) -> None:
        """Reset per-session transient filter state."""
        self.state.tree_filter_loading = False
        self.state.tree_filter_collapsed_dirs = set()

    def open_tree_filter(self, mode: str = "files") -> None:
        """Open filter panel in requested mode and initialize session fields."""
        was_active = self.state.tree_filter_active
        previous_mode = self.state.tree_filter_mode
        if not self.state.tree_filter_active:
            self.state.tree_filter_prev_browser_visible = self.state.browser_visible
        was_browser_visible = self.state.browser_visible
        self.state.browser_visible = True
        if self.state.wrap_text and not was_browser_visible:
            self.rebuild_screen_lines()
        self.state.tree_filter_active = True
        self.state.tree_filter_mode = mode
        self.state.tree_filter_editing = True
        self.state.tree_filter_origin = self.current_jump_location() if mode == "content" else None
        self.state.tree_filter_query = ""
        self.state.tree_filter_match_count = 0
        self.state.tree_filter_truncated = False
        self.reset_tree_filter_session_state()
        if was_active and previous_mode != mode:
            self.rebuild_tree_entries(preferred_path=self.state.current_path.resolve())
        self.state.dirty = True
        if self.on_tree_filter_state_change is not None:
            self.on_tree_filter_state_change()

    def close_tree_filter(self, clear_query: bool = True, restore_origin: bool = False) -> None:
        """Close filter panel, optionally restoring original content-search position."""
        previous_browser_visible = self.state.tree_filter_prev_browser_visible
        restore_location: JumpLocation | None = None
        if restore_origin and self.state.tree_filter_mode == "content" and self.state.tree_filter_origin is not None:
            restore_location = self.state.tree_filter_origin.normalized()
        self.state.tree_filter_active = False
        self.state.tree_filter_editing = False
        self.state.tree_filter_mode = "files"
        if clear_query:
            self.state.tree_filter_query = ""
            self.state.tree_filter_truncated = False
        self.reset_tree_filter_session_state()
        self.state.tree_filter_prev_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = self.state.browser_visible != previous_browser_visible
            self.state.browser_visible = previous_browser_visible
            if self.state.wrap_text and browser_visibility_changed:
                self.rebuild_screen_lines()
        if restore_location is not None:
            self.jump_to_path(restore_location.path)
            self.state.max_start = max(0, len(self.state.lines) - self.visible_content_rows())
            self.state.start = max(0, min(restore_location.start, self.state.max_start))
            self.state.text_x = 0 if self.state.wrap_text else max(0, restore_location.text_x)
        else:
            self.rebuild_tree_entries(preferred_path=self.state.current_path.resolve())
        self.state.tree_filter_origin = None
        self.state.dirty = True
        if self.on_tree_filter_state_change is not None:
            self.on_tree_filter_state_change()

    def activate_tree_filter_selection(self) -> None:
        """Activate selected filter result according to current filter mode."""
        if not self.state.tree_entries:
            if self.state.tree_filter_mode == "content":
                self.state.tree_filter_editing = False
                self.state.dirty = True
            else:
                self.close_tree_filter(clear_query=True)
            return

        entry = self.state.tree_entries[self.state.selected_idx]
        if entry.is_dir:
            candidate_idx = self.nearest_tree_filter_result_entry_index(self.state.selected_idx)
            if candidate_idx is None:
                self.close_tree_filter(clear_query=True)
                return
            self.state.selected_idx = candidate_idx
            entry = self.state.tree_entries[self.state.selected_idx]

        selected_path = entry.path.resolve()
        selected_line = entry.line if entry.kind == "search_hit" else None
        if self.state.tree_filter_mode == "content":
            # Keep content-search mode active after Enter/double-click; Esc exits.
            origin = self.current_jump_location()
            self.state.tree_filter_editing = False
            self.preview_selected_entry()
            self.record_jump_if_changed(origin)
            self.state.dirty = True
            return

        origin = self.current_jump_location()
        self.close_tree_filter(clear_query=True)
        self.jump_to_path(selected_path)
        if selected_line is not None:
            self.jump_to_line(max(0, selected_line - 1))
        self.record_jump_if_changed(origin)
        self.state.dirty = True
