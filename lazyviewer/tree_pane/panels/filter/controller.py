"""Tree-filter controller with lifecycle, navigation, and matching logic."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from collections import OrderedDict
from pathlib import Path
from queue import Empty, Queue

from ....runtime.navigation import JumpLocation
from ....runtime.state import AppState
from ....search.fuzzy import (
    STRICT_SUBSTRING_ONLY_MIN_FILES,
    collect_project_file_labels,
    fuzzy_match_label_index,
)
from ....tree_model import (
    build_tree_entries,
    build_workspace_tree_entries,
    filter_tree_entries_for_content_matches,
    filter_tree_entries_for_files,
    find_content_hit_index,
    next_file_entry_index,
)
from ...workspace_roots import (
    normalized_workspace_expanded_sections,
    workspace_root_banner_rows,
)
from . import matching as filter_matching
from .helpers import skip_gitignored_for_hidden_mode
from .limits import (
    CONTENT_SEARCH_CACHE_MAX_QUERIES,
    CONTENT_SEARCH_FILE_LIMIT,
    content_search_match_limit_for_query,
    tree_filter_match_limit_for_query,
)
from .panel import FilterPanel

CONTENT_SEARCH_STREAM_REFRESH_DEBOUNCE_SECONDS = 0.01
CONTENT_SEARCH_CLICK_PROMPT_REVEAL_DELAY_SECONDS = 0.1
CONTENT_SEARCH_CLICK_INITIAL_WAIT_SECONDS = 0.02


class TreeFilterController:
    """Stateful controller for tree-filter lifecycle and navigation."""

    def __init__(
        self,
        *,
        state: AppState,
        visible_content_rows: Callable[[], int],
        rebuild_screen_lines: Callable[..., None],
        preview_selected_entry: Callable[..., None],
        current_jump_location: Callable[[], JumpLocation],
        record_jump_if_changed: Callable[[JumpLocation], None],
        jump_to_path: Callable[[Path], None],
        jump_to_line: Callable[[int], None],
        on_tree_filter_state_change: Callable[[], None] | None = None,
    ) -> None:
        """Create operations object from explicit runtime dependencies."""

        self.state = state
        self.visible_content_rows = visible_content_rows
        self.rebuild_screen_lines = rebuild_screen_lines
        self.preview_selected_entry = preview_selected_entry
        self.current_jump_location = current_jump_location
        self.record_jump_if_changed = record_jump_if_changed
        self.jump_to_path = jump_to_path
        self.jump_to_line = jump_to_line
        self.on_tree_filter_state_change = on_tree_filter_state_change
        self.loading_until = 0.0
        self.init_content_search_cache()
        self._content_search_generation = 0
        self._active_content_search_generation: int | None = None
        self._content_search_cancel_event: threading.Event | None = None
        self._content_search_worker: threading.Thread | None = None
        self._content_search_events: Queue[tuple[object, ...]] = Queue()
        self._streaming_matches_by_file: dict[Path, list[filter_matching.ContentMatch]] = {}
        self._streaming_truncated = False
        self._streaming_preferred_path: Path | None = None
        self._streaming_force_first_file = False
        self._streaming_preview_selection = False
        self._streaming_partial_dirty = False
        self._streaming_last_match_at = 0.0
        self._content_search_prompt_reveal_at = 0.0
        self._streaming_initial_rebuild_pending = False
        self.panel = FilterPanel(self)

    # lifecycle
    def get_loading_until(self) -> float:
        """Return timestamp until which loading indicator should remain visible."""
        if self._active_content_search_generation is not None:
            return float("inf")
        return self.loading_until

    def tree_filter_prompt_prefix(self) -> str:
        """Return prompt prefix for active filter mode."""
        return "/>" if self.state.tree_filter_mode == "content" else "p>"

    def tree_filter_placeholder(self) -> str:
        """Return placeholder text for active filter mode."""
        return "type to search content" if self.state.tree_filter_mode == "content" else "type to filter files"

    def set_tree_filter_prompt_row_visible(self, visible: bool) -> None:
        """Show or hide the filter prompt row without changing filter state."""
        if visible:
            self._content_search_prompt_reveal_at = 0.0
        if self.state.tree_filter_prompt_row_visible == visible:
            return
        self.state.tree_filter_prompt_row_visible = visible
        self.state.dirty = True

    def tree_view_rows(self) -> int:
        """Return visible tree rows after subtracting root-banner and prompt rows."""
        rows = self.visible_content_rows()
        rows -= workspace_root_banner_rows(
            self.state.tree_roots,
            self.state.tree_root,
            picker_active=self.state.picker_active,
        )
        if (
            self.state.tree_filter_active
            and self.state.tree_filter_prompt_row_visible
            and not self.state.picker_active
        ):
            return max(1, rows - 1)
        return max(1, rows)

    def normalized_workspace_expanded(self) -> list[set[Path]]:
        """Return per-root expansion sets and synchronize legacy flat expansion."""
        roots, sections, flat_union = normalized_workspace_expanded_sections(
            self.state.tree_roots,
            self.state.tree_root,
            self.state.workspace_expanded,
            self.state.expanded,
        )
        self.state.tree_roots = roots
        self.state.workspace_expanded = sections
        self.state.expanded = flat_union
        return sections

    def reset_tree_filter_session_state(self) -> None:
        """Reset per-session transient filter state."""
        self.cancel_content_search()
        self._content_search_prompt_reveal_at = 0.0
        self.state.tree_filter_loading = False
        self.state.tree_filter_collapsed_dirs = set()

    def cancel_content_search(self) -> None:
        """Cancel active streaming content search worker if one is running."""
        if self._content_search_cancel_event is not None:
            self._content_search_cancel_event.set()
        self._active_content_search_generation = None
        self._content_search_cancel_event = None
        self._content_search_worker = None
        self._streaming_matches_by_file = {}
        self._streaming_truncated = False
        self._streaming_preferred_path = None
        self._streaming_force_first_file = False
        self._streaming_preview_selection = False
        self._streaming_partial_dirty = False
        self._streaming_last_match_at = 0.0
        self._content_search_prompt_reveal_at = 0.0
        self._streaming_initial_rebuild_pending = False
        self.loading_until = 0.0
        if self.state.tree_filter_loading:
            self.state.tree_filter_loading = False
            self.state.dirty = True

    def _store_content_search_cache(
        self,
        key: tuple[tuple[str, ...], str, bool, bool, int, int],
        result: tuple[dict[Path, list[filter_matching.ContentMatch]], bool, str | None],
    ) -> None:
        """Insert content-search result into LRU cache and enforce max size."""
        self.content_search_cache[key] = result
        self.content_search_cache.move_to_end(key)
        while len(self.content_search_cache) > CONTENT_SEARCH_CACHE_MAX_QUERIES:
            self.content_search_cache.popitem(last=False)

    def _start_streaming_content_search(
        self,
        *,
        query: str,
        max_matches: int,
        cache_key: tuple[tuple[str, ...], str, bool, bool, int, int],
        preferred_path: Path | None,
        force_first_file: bool,
        preview_selection: bool,
    ) -> None:
        """Spawn background rg worker and stream partial matches through queue events."""
        self.cancel_content_search()
        self._content_search_generation += 1
        generation = self._content_search_generation
        cancel_event = threading.Event()
        self._active_content_search_generation = generation
        self._content_search_cancel_event = cancel_event
        self._streaming_matches_by_file = {}
        self._streaming_truncated = False
        self._streaming_preferred_path = preferred_path
        self._streaming_force_first_file = force_first_file
        self._streaming_preview_selection = preview_selection
        self._streaming_partial_dirty = False
        self._streaming_last_match_at = 0.0
        self._streaming_initial_rebuild_pending = False
        self.loading_until = float("inf")
        self.state.tree_filter_loading = True

        self.normalized_workspace_expanded()
        roots = list(self.state.tree_roots)
        show_hidden = self.state.show_hidden
        skip_gitignored = skip_gitignored_for_hidden_mode(show_hidden)

        def on_match(
            match_path: Path,
            match: filter_matching.ContentMatch,
            _total_matches: int,
            _total_files: int,
        ) -> None:
            if cancel_event.is_set():
                return
            self._content_search_events.put(("match", generation, match_path, match))

        def run_worker() -> None:
            result = self.search_workspace_content_rg(
                roots=roots,
                query=query,
                show_hidden=show_hidden,
                skip_gitignored=skip_gitignored,
                max_matches=max(1, max_matches),
                on_match=on_match,
                should_cancel=cancel_event.is_set,
            )
            self._content_search_events.put(("done", generation, cache_key, result))

        worker = threading.Thread(
            target=run_worker,
            name=f"lazyviewer-content-search-{generation}",
            daemon=True,
        )
        self._content_search_worker = worker
        worker.start()

    def poll_content_search_updates(self, timeout_seconds: float = 0.0) -> bool:
        """Drain queued streaming-search events and refresh tree results incrementally."""
        processed = False
        final_result: tuple[
            tuple[tuple[str, ...], str, bool, bool, int, int],
            tuple[dict[Path, list[filter_matching.ContentMatch]], bool, str | None],
        ] | None = None

        def consume_event(event: tuple[object, ...]) -> None:
            nonlocal processed
            nonlocal final_result
            processed = True
            kind = event[0]
            generation = event[1]
            if generation != self._active_content_search_generation:
                return
            if kind == "match":
                _kind, _generation, match_path, match = event
                bucket = self._streaming_matches_by_file.setdefault(match_path, [])
                bucket.append(match)
                self._streaming_partial_dirty = True
                self._streaming_last_match_at = time.monotonic()
                return
            if kind == "done":
                _kind, _generation, cache_key, result = event
                final_result = (cache_key, result)
                return

        if timeout_seconds > 0:
            try:
                first_event = self._content_search_events.get(timeout=timeout_seconds)
            except Empty:
                first_event = None
            if first_event is not None:
                consume_event(first_event)

        while True:
            try:
                event = self._content_search_events.get_nowait()
            except Empty:
                break
            consume_event(event)

        if (
            final_result is None
            and self._active_content_search_generation is not None
            and self._content_search_prompt_reveal_at > 0.0
            and not self.state.tree_filter_prompt_row_visible
            and self.state.tree_filter_active
            and self.state.tree_filter_mode == "content"
            and time.monotonic() >= self._content_search_prompt_reveal_at
        ):
            self.state.tree_filter_prompt_row_visible = True
            self._content_search_prompt_reveal_at = 0.0
            if self._streaming_initial_rebuild_pending:
                if self._streaming_matches_by_file:
                    self.rebuild_tree_entries(
                        preferred_path=self._streaming_preferred_path,
                        force_first_file=self._streaming_force_first_file,
                        content_matches_override=self._streaming_matches_by_file,
                        content_truncated_override=self._streaming_truncated,
                    )
                    self._streaming_partial_dirty = False
                else:
                    self.rebuild_tree_entries(
                        preferred_path=self._streaming_preferred_path,
                        force_first_file=self._streaming_force_first_file,
                        content_matches_override={},
                        content_truncated_override=False,
                    )
                self._streaming_initial_rebuild_pending = False
            self.state.dirty = True
            processed = True

        if (
            final_result is None
            and self._streaming_partial_dirty
            and self.state.tree_filter_active
            and self.state.tree_filter_mode == "content"
        ):
            now = time.monotonic()
            if now - self._streaming_last_match_at >= CONTENT_SEARCH_STREAM_REFRESH_DEBOUNCE_SECONDS:
                self.rebuild_tree_entries(
                    preferred_path=self._streaming_preferred_path,
                    force_first_file=self._streaming_force_first_file,
                    content_matches_override=self._streaming_matches_by_file,
                    content_truncated_override=self._streaming_truncated,
                )
                self._streaming_partial_dirty = False
                self._streaming_initial_rebuild_pending = False
                self.state.dirty = True

        if final_result is not None and self.state.tree_filter_mode == "content":
            self._content_search_prompt_reveal_at = 0.0
            cache_key, result = final_result
            matches_by_file, truncated, _error = result
            self._store_content_search_cache(cache_key, result)
            self._streaming_matches_by_file = matches_by_file
            self._streaming_truncated = truncated
            self._streaming_partial_dirty = False
            self._streaming_last_match_at = 0.0
            self._streaming_initial_rebuild_pending = False
            self.rebuild_tree_entries(
                preferred_path=self._streaming_preferred_path,
                force_first_file=self._streaming_force_first_file,
                content_matches_override=matches_by_file,
                content_truncated_override=truncated,
            )
            if self._streaming_preview_selection:
                self.preview_selected_entry(force=True)
            self._active_content_search_generation = None
            self._content_search_cancel_event = None
            self._content_search_worker = None
            self.loading_until = 0.0
            self.state.tree_filter_loading = False
            self.state.dirty = True

        return processed

    def open_tree_filter(self, mode: str = "files") -> None:
        """Open filter panel in requested mode and initialize session fields."""
        self.panel.open(mode)

    def toggle_tree_filter_mode(self, mode: str) -> None:
        """Open/switch/close tree filter UI based on current editing state."""
        self.panel.toggle_mode(mode)

    def close_tree_filter(self, clear_query: bool = True, restore_origin: bool = False) -> None:
        """Close filter panel, optionally restoring original content-search position."""
        self.panel.close(clear_query=clear_query, restore_origin=restore_origin)

    def activate_tree_filter_selection(self) -> None:
        """Activate selected filter result according to current filter mode."""
        self.panel.activate_selection()

    def handle_tree_filter_key(
        self,
        key: str,
        *,
        handle_tree_mouse_wheel: Callable[[str], bool],
        handle_tree_mouse_click: Callable[[str], bool],
        toggle_help_panel: Callable[[], None],
    ) -> bool:
        """Handle one key for tree-filter prompt, list navigation, and hit jumps."""
        return self.panel.handle_key(
            key,
            handle_tree_mouse_wheel=handle_tree_mouse_wheel,
            handle_tree_mouse_click=handle_tree_mouse_click,
            toggle_help_panel=toggle_help_panel,
        )

    # navigation
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

    # matching/cache
    @staticmethod
    def _workspace_roots_signature(roots: list[Path]) -> tuple[str, ...]:
        """Build cache signature preserving root order and duplicate sections."""
        return tuple(str(root.resolve()) for root in roots)

    def _collect_workspace_file_labels_parallel(
        self,
        roots: list[Path],
        *,
        show_hidden: bool,
        skip_gitignored: bool,
    ) -> list[list[str]]:
        """Collect per-root label indexes in parallel and keep section order stable."""
        if not roots:
            return []

        labels_by_section: list[list[str]] = [[] for _ in roots]

        if len(roots) == 1:
            labels_by_section[0] = collect_project_file_labels(
                roots[0],
                show_hidden,
                skip_gitignored=skip_gitignored,
            )
            return labels_by_section

        max_workers = min(8, len(roots))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="lazyviewer-file-index") as executor:
            futures = [
                executor.submit(
                    collect_project_file_labels,
                    root,
                    show_hidden,
                    skip_gitignored=skip_gitignored,
                )
                for root in roots
            ]
            for section_idx, future in enumerate(futures):
                try:
                    labels_by_section[section_idx] = future.result()
                except Exception:
                    labels_by_section[section_idx] = []

        return labels_by_section

    def search_workspace_content_rg(
        self,
        roots: list[Path],
        query: str,
        show_hidden: bool,
        *,
        skip_gitignored: bool = False,
        max_matches: int = 2_000,
        on_match: Callable[[Path, filter_matching.ContentMatch, int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[dict[Path, list[filter_matching.ContentMatch]], bool, str | None]:
        """Search content across all workspace roots and merge deduplicated hits."""
        normalized_roots = [root.resolve() for root in roots]
        if not normalized_roots:
            return {}, False, None

        max_total_matches = max(1, max_matches)
        seen_event_keys: set[tuple[str, int, int, str]] = set()
        seen_event_files: set[str] = set()
        seen_lock = threading.Lock()
        stream_truncated = threading.Event()
        local_cancel = threading.Event()
        streamed_matches = 0

        def cancelled() -> bool:
            return local_cancel.is_set() or (should_cancel is not None and should_cancel())

        def emit_unique_match(path: Path, match: filter_matching.ContentMatch) -> None:
            nonlocal streamed_matches
            match_path = path.resolve()
            event_key = (str(match_path), match.line, match.column, match.preview)
            with seen_lock:
                if event_key in seen_event_keys:
                    return
                if streamed_matches >= max_total_matches:
                    stream_truncated.set()
                    local_cancel.set()
                    return
                file_key = str(match_path)
                if file_key not in seen_event_files and len(seen_event_files) >= CONTENT_SEARCH_FILE_LIMIT:
                    stream_truncated.set()
                    local_cancel.set()
                    return
                seen_event_keys.add(event_key)
                seen_event_files.add(file_key)
                streamed_matches += 1
                total_matches = streamed_matches
                total_files = len(seen_event_files)
            if on_match is not None:
                try:
                    on_match(match_path, match, total_matches, total_files)
                except Exception:
                    pass

        def search_one_root(root: Path) -> tuple[dict[Path, list[filter_matching.ContentMatch]], bool, str | None]:
            def root_on_match(
                match_path: Path,
                match: filter_matching.ContentMatch,
                _total_matches: int,
                _total_files: int,
            ) -> None:
                if cancelled():
                    return
                emit_unique_match(match_path, match)

            return filter_matching.search_project_content_rg(
                root,
                query,
                show_hidden,
                skip_gitignored=skip_gitignored,
                max_matches=max_total_matches,
                max_files=CONTENT_SEARCH_FILE_LIMIT,
                on_match=root_on_match,
                should_cancel=cancelled,
            )

        if len(normalized_roots) == 1:
            single_result = search_one_root(normalized_roots[0])
            merged_matches, merged_truncated, merged_error = self._merge_workspace_content_search_results(
                normalized_roots,
                [single_result],
                max_total_matches=max_total_matches,
                stream_truncated=stream_truncated.is_set(),
            )
            return merged_matches, merged_truncated, merged_error

        max_workers = min(8, len(normalized_roots))
        root_results: list[tuple[dict[Path, list[filter_matching.ContentMatch]], bool, str | None]] = [
            ({}, False, None) for _ in normalized_roots
        ]
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="lazyviewer-content-search",
        ) as executor:
            futures = [executor.submit(search_one_root, root) for root in normalized_roots]
            for section_idx, future in enumerate(futures):
                try:
                    root_results[section_idx] = future.result()
                except Exception as exc:
                    root_results[section_idx] = ({}, False, f"failed to run rg: {exc}")

        return self._merge_workspace_content_search_results(
            normalized_roots,
            root_results,
            max_total_matches=max_total_matches,
            stream_truncated=stream_truncated.is_set(),
        )

    @staticmethod
    def _merge_workspace_content_search_results(
        roots: list[Path],
        root_results: list[tuple[dict[Path, list[filter_matching.ContentMatch]], bool, str | None]],
        *,
        max_total_matches: int,
        stream_truncated: bool,
    ) -> tuple[dict[Path, list[filter_matching.ContentMatch]], bool, str | None]:
        """Merge per-root content-search results with global dedupe and limits."""
        merged_matches: dict[Path, list[filter_matching.ContentMatch]] = {}
        seen_match_keys: set[tuple[str, int, int, str]] = set()
        file_order: list[Path] = []
        total_matches = 0
        truncated = stream_truncated
        errors: list[str] = []

        for section_idx, result in enumerate(root_results):
            matches_by_file, root_truncated, root_error = result
            root = roots[section_idx] if section_idx < len(roots) else None
            if root_error:
                if root is not None:
                    errors.append(f"{root}: {root_error}")
                else:
                    errors.append(root_error)
            if root_truncated:
                truncated = True

            for match_path in sorted(matches_by_file, key=lambda item: str(item).casefold()):
                resolved_path = match_path.resolve()
                for match in matches_by_file[match_path]:
                    match_key = (str(resolved_path), match.line, match.column, match.preview)
                    if match_key in seen_match_keys:
                        continue
                    if total_matches >= max_total_matches:
                        truncated = True
                        break
                    if resolved_path not in merged_matches and len(merged_matches) >= CONTENT_SEARCH_FILE_LIMIT:
                        truncated = True
                        break
                    seen_match_keys.add(match_key)
                    if resolved_path not in merged_matches:
                        merged_matches[resolved_path] = []
                        file_order.append(resolved_path)
                    merged_matches[resolved_path].append(match)
                    total_matches += 1
                if truncated and total_matches >= max_total_matches:
                    break
                if truncated and resolved_path not in merged_matches and len(merged_matches) >= CONTENT_SEARCH_FILE_LIMIT:
                    break

        ordered_matches: dict[Path, list[filter_matching.ContentMatch]] = {}
        for path in file_order:
            ordered_items = sorted(
                merged_matches[path],
                key=lambda item: (item.line, item.column, item.preview),
            )
            ordered_matches[path] = ordered_items

        if ordered_matches:
            return ordered_matches, truncated, None
        if errors:
            return {}, truncated, errors[0]
        return {}, truncated, None

    def refresh_tree_filter_file_index(self) -> None:
        """Refresh cached file-label index when roots/hidden-mode change."""
        roots, sections, flat_union = normalized_workspace_expanded_sections(
            self.state.tree_roots,
            self.state.tree_root,
            self.state.workspace_expanded,
            self.state.expanded,
        )
        self.state.tree_roots = roots
        self.state.workspace_expanded = sections
        self.state.expanded = flat_union

        roots_signature = self._workspace_roots_signature(roots)
        if (
            self.state.picker_files_roots_signature == roots_signature
            and self.state.picker_files_show_hidden == self.state.show_hidden
        ):
            return

        skip_gitignored = skip_gitignored_for_hidden_mode(self.state.show_hidden)
        labels_by_section = self._collect_workspace_file_labels_parallel(
            roots,
            show_hidden=self.state.show_hidden,
            skip_gitignored=skip_gitignored,
        )
        labels: list[str] = []
        file_paths: list[Path] = []
        file_sections: list[int] = []
        for section_idx, root in enumerate(roots):
            section_labels = labels_by_section[section_idx] if section_idx < len(labels_by_section) else []
            for label in section_labels:
                labels.append(label)
                file_paths.append(root / label)
                file_sections.append(section_idx)

        self.state.picker_file_labels = labels
        self.state.picker_file_paths = file_paths
        self.state.picker_file_workspace_sections = file_sections
        self.state.picker_file_labels_folded = []
        self.state.picker_files_root = roots[0] if roots else self.state.tree_root.resolve()
        self.state.picker_files_roots_signature = roots_signature
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

    def content_search_cache_key(self, query: str, max_matches: int) -> tuple[tuple[str, ...], str, bool, bool, int, int]:
        """Build stable cache key for one content-search request."""
        skip_gitignored = skip_gitignored_for_hidden_mode(self.state.show_hidden)
        self.normalized_workspace_expanded()
        roots_signature = self._workspace_roots_signature(self.state.tree_roots)
        return (
            roots_signature,
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
    ) -> tuple[dict[Path, list[filter_matching.ContentMatch]], bool, str | None]:
        """Run content search with LRU caching by query/roots/mode/limits."""
        key = self.content_search_cache_key(query, max_matches)
        cached = self.content_search_cache.get(key)
        if cached is not None:
            self.content_search_cache.move_to_end(key)
            return cached

        self.normalized_workspace_expanded()
        result = self.search_workspace_content_rg(
            roots=list(self.state.tree_roots),
            query=query,
            show_hidden=self.state.show_hidden,
            skip_gitignored=skip_gitignored_for_hidden_mode(self.state.show_hidden),
            max_matches=max(1, max_matches),
        )
        self._store_content_search_cache(key, result)
        return result

    def rebuild_tree_entries(
        self,
        preferred_path: Path | None = None,
        center_selection: bool = False,
        force_first_file: bool = False,
        preferred_workspace_root: Path | None = None,
        preferred_workspace_section: int | None = None,
        content_matches_override: dict[Path, list[filter_matching.ContentMatch]] | None = None,
        content_truncated_override: bool | None = None,
    ) -> None:
        """Rebuild tree entries for current filter state and preserve intent."""
        previous_selected_hit_path: Path | None = None
        previous_selected_hit_line: int | None = None
        previous_selected_hit_column: int | None = None
        previous_selected_path: Path | None = None
        previous_selected_workspace_root: Path | None = None
        previous_selected_workspace_section: int | None = None
        if self.state.tree_entries and 0 <= self.state.selected_idx < len(self.state.tree_entries):
            previous_entry = self.state.tree_entries[self.state.selected_idx]
            previous_selected_path = previous_entry.path.resolve()
            if previous_entry.workspace_root is not None:
                previous_selected_workspace_root = previous_entry.workspace_root.resolve()
            previous_selected_workspace_section = previous_entry.workspace_section
            if previous_entry.kind == "search_hit":
                previous_selected_hit_path = previous_entry.path.resolve()
                previous_selected_hit_line = previous_entry.line
                previous_selected_hit_column = previous_entry.column

        if preferred_path is None:
            if self.state.tree_entries and 0 <= self.state.selected_idx < len(self.state.tree_entries):
                preferred_path = self.state.tree_entries[self.state.selected_idx].path.resolve()
            else:
                preferred_path = self.state.current_path.resolve()

        preferred_target = preferred_path.resolve()
        preferred_workspace_scope = preferred_workspace_root.resolve() if preferred_workspace_root is not None else None
        preferred_workspace_scope_section = preferred_workspace_section
        if (
            preferred_workspace_scope is None
            and previous_selected_path is not None
            and previous_selected_workspace_root is not None
            and previous_selected_path == preferred_target
        ):
            preferred_workspace_scope = previous_selected_workspace_root
        if (
            preferred_workspace_scope_section is None
            and previous_selected_path is not None
            and previous_selected_workspace_section is not None
            and previous_selected_path == preferred_target
        ):
            preferred_workspace_scope_section = previous_selected_workspace_section

        if self.state.tree_filter_active and self.state.tree_filter_query:
            if self.state.tree_filter_mode == "content":
                if content_matches_override is not None:
                    matches_by_file = content_matches_override
                    truncated = bool(content_truncated_override)
                else:
                    match_limit = self.content_search_match_limit(self.state.tree_filter_query)
                    matches_by_file, truncated, _error = self.search_project_content_cached(
                        self.state.tree_filter_query,
                        match_limit,
                    )
                self.state.tree_filter_match_count = sum(len(items) for items in matches_by_file.values())
                self.state.tree_filter_truncated = truncated
                workspace_expanded = self.normalized_workspace_expanded()
                all_entries = []
                render_expanded: set[Path] = set()
                for section_idx, root in enumerate(self.state.tree_roots):
                    section_entries, section_expanded = filter_tree_entries_for_content_matches(
                        root,
                        workspace_expanded[section_idx] if section_idx < len(workspace_expanded) else self.state.expanded,
                        matches_by_file,
                        collapsed_dirs=self.state.tree_filter_collapsed_dirs,
                        workspace_root=root,
                        workspace_section=section_idx,
                    )
                    all_entries.extend(section_entries)
                    render_expanded.update(section_expanded)
                self.state.tree_entries = all_entries
                self.state.tree_render_expanded = render_expanded
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
                matched_paths_by_section: dict[int, list[Path]] = {}
                for index, _label, _score in matched:
                    if not (0 <= index < len(self.state.picker_file_paths)):
                        continue
                    section_idx = (
                        self.state.picker_file_workspace_sections[index]
                        if index < len(self.state.picker_file_workspace_sections)
                        else 0
                    )
                    matched_paths_by_section.setdefault(section_idx, []).append(self.state.picker_file_paths[index])

                roots, sections, flat_union = normalized_workspace_expanded_sections(
                    self.state.tree_roots,
                    self.state.tree_root,
                    self.state.workspace_expanded,
                    self.state.expanded,
                )
                self.state.tree_roots = roots
                self.state.workspace_expanded = sections
                self.state.expanded = flat_union

                all_entries = []
                render_expanded: set[Path] = set()
                skip_gitignored = skip_gitignored_for_hidden_mode(self.state.show_hidden)
                for section_idx, root in enumerate(roots):
                    section_entries, section_expanded = filter_tree_entries_for_files(
                        root,
                        sections[section_idx] if section_idx < len(sections) else self.state.expanded,
                        self.state.show_hidden,
                        matched_paths_by_section.get(section_idx, []),
                        skip_gitignored=skip_gitignored,
                        workspace_root=root,
                        workspace_section=section_idx,
                    )
                    all_entries.extend(section_entries)
                    render_expanded.update(section_expanded)

                self.state.tree_filter_match_count = sum(len(paths) for paths in matched_paths_by_section.values())
                self.state.tree_entries = all_entries
                self.state.tree_render_expanded = render_expanded
        else:
            self.state.tree_filter_match_count = 0
            self.state.tree_filter_truncated = False
            workspace_expanded = self.normalized_workspace_expanded()
            self.state.tree_render_expanded = set(self.state.expanded)
            self.state.tree_entries = build_workspace_tree_entries(
                self.state.tree_roots,
                self.state.tree_root,
                self.state.expanded,
                workspace_expanded,
                self.state.show_hidden,
                skip_gitignored=skip_gitignored_for_hidden_mode(self.state.show_hidden),
            )

        if force_first_file:
            first_idx = self.next_tree_filter_result_entry_index(-1, 1)
            self.state.selected_idx = first_idx if first_idx is not None else 0
        else:
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
                    preferred_workspace_section=previous_selected_workspace_section,
                )
                if preserved_hit_idx is not None:
                    self.state.selected_idx = preserved_hit_idx
                    matched_preferred = True

            if not matched_preferred:
                first_match_idx: int | None = None
                root_match_idx: int | None = None
                scoped_section_match_idx: int | None = None
                scoped_root_match_idx: int | None = None
                for idx, entry in enumerate(self.state.tree_entries):
                    if entry.kind == "search_hit":
                        continue
                    if entry.path.resolve() != preferred_target:
                        continue
                    if first_match_idx is None:
                        first_match_idx = idx
                    entry_root = entry.workspace_root.resolve() if entry.workspace_root is not None else None
                    entry_section = entry.workspace_section
                    if (
                        preferred_workspace_scope_section is not None
                        and entry_section is not None
                        and entry_section == preferred_workspace_scope_section
                        and scoped_section_match_idx is None
                    ):
                        scoped_section_match_idx = idx
                    if (
                        preferred_workspace_scope is not None
                        and entry_root is not None
                        and entry_root == preferred_workspace_scope
                        and scoped_root_match_idx is None
                    ):
                        scoped_root_match_idx = idx
                    if entry_root is not None and entry_root == preferred_target and entry.depth == 0:
                        root_match_idx = idx
                chosen_idx = (
                    scoped_section_match_idx
                    if scoped_section_match_idx is not None
                    else (
                        scoped_root_match_idx
                        if scoped_root_match_idx is not None
                        else root_match_idx if root_match_idx is not None else first_match_idx
                    )
                )
                if chosen_idx is not None:
                    self.state.selected_idx = chosen_idx
                    matched_preferred = True

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
        debounce_prompt_row: bool = False,
    ) -> None:
        """Apply query text, rebuild results, and update loading indicator timing."""
        self.state.tree_filter_query = query
        force_first_file = select_first_file and bool(query)
        preferred_path = None if force_first_file else self.state.current_path.resolve()
        suppress_prompt_row = bool(
            debounce_prompt_row
            and bool(query)
            and self.state.tree_filter_mode == "content"
        )

        if self.state.tree_filter_mode != "content":
            self.cancel_content_search()
            self.set_tree_filter_prompt_row_visible(True)
            self.loading_until = 0.0 if not query else time.monotonic() + 0.35
            self.rebuild_tree_entries(
                preferred_path=preferred_path,
                force_first_file=force_first_file,
            )
            if preview_selection:
                self.preview_selected_entry(force=True)
            self.state.dirty = True
            if self.on_tree_filter_state_change is not None:
                self.on_tree_filter_state_change()
            return

        if not query:
            self.cancel_content_search()
            self.set_tree_filter_prompt_row_visible(True)
            self.loading_until = 0.0
            self.rebuild_tree_entries(
                preferred_path=preferred_path,
                force_first_file=force_first_file,
                content_matches_override={},
                content_truncated_override=False,
            )
            self.state.tree_filter_loading = False
            if preview_selection:
                self.preview_selected_entry(force=True)
            self.state.dirty = True
            if self.on_tree_filter_state_change is not None:
                self.on_tree_filter_state_change()
            return

        match_limit = self.content_search_match_limit(query)
        cache_key = self.content_search_cache_key(query, match_limit)
        cached = self.content_search_cache.get(cache_key)
        if cached is not None:
            self.cancel_content_search()
            if suppress_prompt_row:
                self._content_search_prompt_reveal_at = 0.0
                self._streaming_initial_rebuild_pending = False
                self.set_tree_filter_prompt_row_visible(False)
            else:
                self.set_tree_filter_prompt_row_visible(True)
            self.loading_until = 0.0
            matches_by_file, truncated, _error = cached
            self.content_search_cache.move_to_end(cache_key)
            self.rebuild_tree_entries(
                preferred_path=preferred_path,
                force_first_file=force_first_file,
                content_matches_override=matches_by_file,
                content_truncated_override=truncated,
            )
            if preview_selection:
                self.preview_selected_entry(force=True)
            self.state.tree_filter_loading = False
            self.state.dirty = True
            if self.on_tree_filter_state_change is not None:
                self.on_tree_filter_state_change()
            return

        if suppress_prompt_row:
            self.set_tree_filter_prompt_row_visible(False)
        else:
            self.set_tree_filter_prompt_row_visible(True)
            self._content_search_prompt_reveal_at = 0.0

        self._start_streaming_content_search(
            query=query,
            max_matches=match_limit,
            cache_key=cache_key,
            preferred_path=preferred_path,
            force_first_file=force_first_file,
            preview_selection=preview_selection,
        )
        if suppress_prompt_row:
            self._streaming_initial_rebuild_pending = True
            self._content_search_prompt_reveal_at = time.monotonic() + CONTENT_SEARCH_CLICK_PROMPT_REVEAL_DELAY_SECONDS
        else:
            self._streaming_initial_rebuild_pending = False
            self.rebuild_tree_entries(
                preferred_path=preferred_path,
                force_first_file=force_first_file,
                content_matches_override={},
                content_truncated_override=False,
            )
        # For click-triggered searches, wait a tiny bit longer so fast rg completions
        # can render directly without an intermediate frame.
        poll_timeout = CONTENT_SEARCH_CLICK_INITIAL_WAIT_SECONDS if suppress_prompt_row else 0.005
        self.poll_content_search_updates(timeout_seconds=poll_timeout)
        self.state.dirty = True
        if self.on_tree_filter_state_change is not None:
            self.on_tree_filter_state_change()

    def init_content_search_cache(self) -> None:
        """Initialize search cache storage."""
        self.content_search_cache: OrderedDict[
            tuple[tuple[str, ...], str, bool, bool, int, int],
            tuple[dict[Path, list[filter_matching.ContentMatch]], bool, str | None],
        ] = OrderedDict()
