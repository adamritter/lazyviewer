"""Tree-filter controller with lifecycle, navigation, and matching logic."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from ....session.navigation import JumpLocation
from ....session import SessionState
from ....ports import PreviewSelectedEntry, RebuildScreenLines
from ....search import (
    ContentMatch,
    ContentMatchesAdded,
    ContentSearchFinished,
    ContentSearchJob,
    ContentSearchRequest,
    FileSearchRequest,
    SearchService,
)
from ....tree_model import (
    build_workspace_tree_entries,
    filter_tree_entries_for_content_matches,
    filter_tree_entries_for_files,
    find_content_hit_index,
    next_file_entry_index,
)
from ....workspace import WorkspaceQuery
from ...workspace_roots import (
    normalized_workspace_expanded_sections,
    workspace_root_banner_rows,
)
from .helpers import skip_gitignored_for_hidden_mode
from .limits import CONTENT_SEARCH_FILE_LIMIT, content_search_match_limit_for_query, tree_filter_match_limit_for_query
from .panel import FilterPanel

CONTENT_SEARCH_STREAM_REFRESH_DEBOUNCE_SECONDS = 0.01
CONTENT_SEARCH_CLICK_PROMPT_REVEAL_DELAY_SECONDS = 0.1
CONTENT_SEARCH_CLICK_INITIAL_WAIT_SECONDS = 0.02


class TreeFilterController:
    """Stateful controller for tree-filter lifecycle and navigation."""

    def __init__(
        self,
        *,
        state: SessionState,
        visible_content_rows: Callable[[], int],
        rebuild_screen_lines: RebuildScreenLines,
        preview_selected_entry: PreviewSelectedEntry,
        current_jump_location: Callable[[], JumpLocation],
        record_jump_if_changed: Callable[[JumpLocation], None],
        jump_to_path: Callable[[Path], None],
        jump_to_line: Callable[[int], None],
        on_tree_filter_state_change: Callable[[], None] | None = None,
        search_service: SearchService,
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
        self.search_service = search_service
        self.loading_until = 0.0
        self._content_search_job: ContentSearchJob | None = None
        self._streaming_matches_by_file: dict[Path, list[ContentMatch]] = {}
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
        if self._content_search_job is not None:
            return float("inf")
        return self.loading_until

    def tree_filter_prompt_prefix(self) -> str:
        """Return prompt prefix for active filter mode."""
        return "/>" if self.state.filter.mode == "content" else "p>"

    def tree_filter_placeholder(self) -> str:
        """Return placeholder text for active filter mode."""
        return "type to search content" if self.state.filter.mode == "content" else "type to filter files"

    def set_tree_filter_prompt_row_visible(self, visible: bool) -> None:
        """Show or hide the filter prompt row without changing filter state."""
        if visible:
            self._content_search_prompt_reveal_at = 0.0
        if self.state.filter.prompt_row_visible == visible:
            return
        self.state.filter.prompt_row_visible = visible
        self.state.interface.dirty = True

    def tree_view_rows(self) -> int:
        """Return visible tree rows after subtracting root-banner and prompt rows."""
        rows = self.visible_content_rows()
        rows -= workspace_root_banner_rows(
            self.state.workspace.roots,
            self.state.workspace.active_root,
            picker_active=self.state.picker.active,
        )
        if (
            self.state.filter.active
            and self.state.filter.prompt_row_visible
            and not self.state.picker.active
        ):
            return max(1, rows - 1)
        return max(1, rows)

    def normalized_workspace_expanded(self) -> list[set[Path]]:
        """Return per-root expansion sets and synchronize their render union."""
        roots, sections, flat_union = normalized_workspace_expanded_sections(
            self.state.workspace.roots,
            self.state.workspace.active_root,
            self.state.workspace.expanded_by_root,
            self.state.workspace.expanded,
        )
        self.state.workspace.roots = roots
        self.state.workspace.expanded_by_root = sections
        self.state.workspace.expanded = flat_union
        return sections

    def reset_tree_filter_session_state(self) -> None:
        """Reset per-session transient filter state."""
        self.cancel_content_search()
        self._content_search_prompt_reveal_at = 0.0
        self.state.filter.loading = False
        self.state.filter.collapsed_dirs = set()

    def cancel_content_search(self) -> None:
        """Cancel active streaming content search worker if one is running."""
        if self._content_search_job is not None:
            self._content_search_job.cancel()
        self._content_search_job = None
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
        if self.state.filter.loading:
            self.state.filter.loading = False
            self.state.interface.dirty = True

    def _workspace_snapshot(self):
        """Return a snapshot aligned to the controller's current root state."""
        roots, sections, flat_union = normalized_workspace_expanded_sections(
            self.state.workspace.roots,
            self.state.workspace.active_root,
            self.state.workspace.expanded_by_root,
            self.state.workspace.expanded,
        )
        self.state.workspace.roots = roots
        self.state.workspace.expanded_by_root = sections
        self.state.workspace.expanded = flat_union
        query = WorkspaceQuery.create(
            roots,
            sections,
            show_hidden=self.state.workspace.show_hidden,
            skip_gitignored=skip_gitignored_for_hidden_mode(self.state.workspace.show_hidden),
        )
        snapshot = self.state.workspace.snapshot
        if snapshot is None:
            snapshot = self.search_service.workspace.snapshot(query)
        elif snapshot.query != query:
            snapshot = self.search_service.workspace.refresh(snapshot, query).snapshot
        self.state.workspace.snapshot = snapshot
        return snapshot

    def _content_request(self, query: str, max_matches: int) -> ContentSearchRequest:
        return ContentSearchRequest(
            workspace=self._workspace_snapshot(),
            query=query,
            max_matches=max(1, max_matches),
            max_files=CONTENT_SEARCH_FILE_LIMIT,
        )

    def _start_streaming_content_search(
        self,
        *,
        query: str,
        max_matches: int,
        preferred_path: Path | None,
        force_first_file: bool,
        preview_selection: bool,
    ) -> None:
        """Start a typed, revision-scoped content-search job."""
        self.cancel_content_search()
        self._streaming_matches_by_file = {}
        self._streaming_truncated = False
        self._streaming_preferred_path = preferred_path
        self._streaming_force_first_file = force_first_file
        self._streaming_preview_selection = preview_selection
        self._streaming_partial_dirty = False
        self._streaming_last_match_at = 0.0
        self._streaming_initial_rebuild_pending = False
        self.loading_until = float("inf")
        self.state.filter.loading = True
        self._content_search_job = self.search_service.start_content(
            self._content_request(query, max_matches)
        )

    def poll_content_search_updates(self, timeout_seconds: float = 0.0) -> bool:
        """Apply typed search updates to the filter-session view state."""
        job = self._content_search_job
        if job is None:
            return False
        updates = job.poll(timeout_seconds)
        processed = bool(updates)
        final_result = None
        for update in updates:
            if isinstance(update, ContentMatchesAdded):
                for match in update.matches:
                    self._streaming_matches_by_file.setdefault(match.path.resolve(), []).append(match)
                self._streaming_partial_dirty = True
                self._streaming_last_match_at = time.monotonic()
            elif isinstance(update, ContentSearchFinished):
                final_result = update.result

        if (
            final_result is None
            and self._content_search_job is not None
            and self._content_search_prompt_reveal_at > 0.0
            and not self.state.filter.prompt_row_visible
            and self.state.filter.active
            and self.state.filter.mode == "content"
            and time.monotonic() >= self._content_search_prompt_reveal_at
        ):
            self.state.filter.prompt_row_visible = True
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
            self.state.interface.dirty = True
            processed = True

        if (
            final_result is None
            and self._streaming_partial_dirty
            and self.state.filter.active
            and self.state.filter.mode == "content"
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
                self.state.interface.dirty = True

        if final_result is not None and self.state.filter.mode == "content":
            self._content_search_prompt_reveal_at = 0.0
            matches_by_file = final_result.as_dict()
            truncated = final_result.truncated
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
            self._content_search_job = None
            self.loading_until = 0.0
            self.state.filter.loading = False
            self.state.interface.dirty = True

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
        if not self.state.workspace.entries or direction == 0:
            return None
        step = 1 if direction > 0 else -1
        idx = selected_idx + step
        while 0 <= idx < len(self.state.workspace.entries):
            if self.state.workspace.entries[idx].kind == "search_hit":
                return idx
            idx += step
        return None

    def next_tree_filter_result_entry_index(self, selected_idx: int, direction: int) -> int | None:
        """Return next result row index for active filter mode."""
        if self.state.filter.mode == "content":
            return self.next_content_hit_entry_index(selected_idx, direction)
        return next_file_entry_index(self.state.workspace.entries, selected_idx, direction)

    def nearest_tree_filter_result_entry_index(self, selected_idx: int) -> int | None:
        """Return closest result row index around selected index."""
        candidate_idx = self.next_tree_filter_result_entry_index(selected_idx, 1)
        if candidate_idx is None:
            candidate_idx = self.next_tree_filter_result_entry_index(selected_idx, -1)
        return candidate_idx

    def coerce_tree_filter_result_index(self, idx: int) -> int | None:
        """Coerce arbitrary row index onto nearest selectable filter result."""
        if not (0 <= idx < len(self.state.workspace.entries)):
            return None
        if not (self.state.filter.active and self.state.filter.query):
            return idx

        entry = self.state.workspace.entries[idx]
        if self.state.filter.mode == "content":
            if entry.kind == "search_hit":
                return idx
        elif not entry.is_dir:
            return idx

        return self.nearest_tree_filter_result_entry_index(idx)

    def move_tree_selection(
        self,
        direction: int,
        *,
        preview_selection: bool = True,
    ) -> bool:
        """Move tree selection, honoring filter-result-only navigation when active."""
        if not self.state.workspace.entries or direction == 0:
            return False

        if self.state.filter.active and self.state.filter.query:
            target_idx = self.state.workspace.selected
            step = 1 if direction > 0 else -1
            for _ in range(abs(direction)):
                next_idx = self.next_tree_filter_result_entry_index(target_idx, step)
                if next_idx is None:
                    break
                target_idx = next_idx
        else:
            target_idx = max(
                0,
                min(
                    len(self.state.workspace.entries) - 1,
                    self.state.workspace.selected + direction,
                ),
            )

        if target_idx == self.state.workspace.selected:
            return False

        self.state.workspace.selected = target_idx
        if preview_selection:
            self.preview_selected_entry()
        return True

    def jump_to_next_content_hit(self, direction: int) -> bool:
        """Jump to next/previous content hit with wrap-around behavior."""
        if direction == 0:
            return False
        if direction > 0:
            target_idx = self.next_content_hit_entry_index(self.state.workspace.selected, 1)
            if target_idx is None:
                target_idx = self.next_content_hit_entry_index(-1, 1)
        else:
            target_idx = self.next_content_hit_entry_index(self.state.workspace.selected, -1)
            if target_idx is None:
                target_idx = self.next_content_hit_entry_index(len(self.state.workspace.entries), -1)

        if target_idx is None or target_idx == self.state.workspace.selected:
            return False

        origin = self.current_jump_location()
        self.state.workspace.selected = target_idx
        self.preview_selected_entry()
        self.record_jump_if_changed(origin)
        return True

    def default_selected_index(self, prefer_files: bool = False) -> int:
        """Return default selected tree index after (re)building entries."""
        if not self.state.workspace.entries:
            return 0
        if prefer_files:
            for idx, entry in enumerate(self.state.workspace.entries):
                if not entry.is_dir:
                    return idx
        if len(self.state.workspace.entries) > 1:
            return 1
        return 0

    def tree_filter_match_limit(self, query: str) -> int:
        """Return adaptive file-filter match cap based on query length."""
        return tree_filter_match_limit_for_query(query)

    def content_search_match_limit(self, query: str) -> int:
        """Return adaptive content-search match cap based on query length."""
        return content_search_match_limit_for_query(query)

    def rebuild_tree_entries(
        self,
        preferred_path: Path | None = None,
        center_selection: bool = False,
        force_first_file: bool = False,
        preferred_workspace_root: Path | None = None,
        preferred_workspace_section: int | None = None,
        content_matches_override: dict[Path, list[ContentMatch]] | None = None,
        content_truncated_override: bool | None = None,
    ) -> None:
        """Rebuild tree entries for current filter state and preserve intent."""
        previous_selected_hit_path: Path | None = None
        previous_selected_hit_line: int | None = None
        previous_selected_hit_column: int | None = None
        previous_selected_path: Path | None = None
        previous_selected_workspace_root: Path | None = None
        previous_selected_workspace_section: int | None = None
        if self.state.workspace.entries and 0 <= self.state.workspace.selected < len(self.state.workspace.entries):
            previous_entry = self.state.workspace.entries[self.state.workspace.selected]
            previous_selected_path = previous_entry.path.resolve()
            if previous_entry.workspace_root is not None:
                previous_selected_workspace_root = previous_entry.workspace_root.resolve()
            previous_selected_workspace_section = previous_entry.workspace_section
            if previous_entry.kind == "search_hit":
                previous_selected_hit_path = previous_entry.path.resolve()
                previous_selected_hit_line = previous_entry.line
                previous_selected_hit_column = previous_entry.column

        if preferred_path is None:
            if self.state.workspace.entries and 0 <= self.state.workspace.selected < len(self.state.workspace.entries):
                preferred_path = self.state.workspace.entries[self.state.workspace.selected].path.resolve()
            else:
                preferred_path = self.state.workspace.current_path.resolve()

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

        if self.state.filter.active and self.state.filter.query:
            if self.state.filter.mode == "content":
                if content_matches_override is not None:
                    matches_by_file = content_matches_override
                    truncated = bool(content_truncated_override)
                else:
                    match_limit = self.content_search_match_limit(self.state.filter.query)
                    cached = self.search_service.cached_content(
                        self._content_request(self.state.filter.query, match_limit)
                    )
                    matches_by_file = cached.as_dict() if cached is not None else dict(self._streaming_matches_by_file)
                    truncated = cached.truncated if cached is not None else self._streaming_truncated
                self.state.filter.match_count = sum(len(items) for items in matches_by_file.values())
                self.state.filter.truncated = truncated
                workspace_expanded = self.normalized_workspace_expanded()
                all_entries = []
                render_expanded: set[Path] = set()
                for section_idx, root in enumerate(self.state.workspace.roots):
                    section_entries, section_expanded = filter_tree_entries_for_content_matches(
                        root,
                        workspace_expanded[section_idx] if section_idx < len(workspace_expanded) else self.state.workspace.expanded,
                        matches_by_file,
                        collapsed_dirs=self.state.filter.collapsed_dirs,
                        workspace_root=root,
                        workspace_section=section_idx,
                    )
                    all_entries.extend(section_entries)
                    render_expanded.update(section_expanded)
                self.state.workspace.entries = all_entries
                self.state.workspace.render_expanded = render_expanded
            else:
                match_limit = self.tree_filter_match_limit(self.state.filter.query)
                raw_matched = self.search_service.match_files(
                    FileSearchRequest(
                        workspace=self._workspace_snapshot(),
                        query=self.state.filter.query,
                        limit=max(1, match_limit + 1),
                    )
                )
                self.state.filter.truncated = len(raw_matched) > match_limit
                matched = raw_matched[:match_limit] if match_limit > 0 else []
                matched_paths_by_section: dict[int, list[Path]] = {}
                for match in matched:
                    matched_paths_by_section.setdefault(match.section, []).append(match.path)

                roots, sections, flat_union = normalized_workspace_expanded_sections(
                    self.state.workspace.roots,
                    self.state.workspace.active_root,
                    self.state.workspace.expanded_by_root,
                    self.state.workspace.expanded,
                )
                self.state.workspace.roots = roots
                self.state.workspace.expanded_by_root = sections
                self.state.workspace.expanded = flat_union

                all_entries = []
                render_expanded: set[Path] = set()
                skip_gitignored = skip_gitignored_for_hidden_mode(self.state.workspace.show_hidden)
                for section_idx, root in enumerate(roots):
                    section_entries, section_expanded = filter_tree_entries_for_files(
                        root,
                        sections[section_idx] if section_idx < len(sections) else self.state.workspace.expanded,
                        self.state.workspace.show_hidden,
                        matched_paths_by_section.get(section_idx, []),
                        skip_gitignored=skip_gitignored,
                        workspace_root=root,
                        workspace_section=section_idx,
                    )
                    all_entries.extend(section_entries)
                    render_expanded.update(section_expanded)

                self.state.filter.match_count = sum(len(paths) for paths in matched_paths_by_section.values())
                self.state.workspace.entries = all_entries
                self.state.workspace.render_expanded = render_expanded
        else:
            self.state.filter.match_count = 0
            self.state.filter.truncated = False
            workspace_expanded = self.normalized_workspace_expanded()
            self.state.workspace.render_expanded = set(self.state.workspace.expanded)
            self.state.workspace.entries = build_workspace_tree_entries(
                self.state.workspace.roots,
                self.state.workspace.active_root,
                self.state.workspace.expanded,
                workspace_expanded,
                self.state.workspace.show_hidden,
                skip_gitignored=skip_gitignored_for_hidden_mode(self.state.workspace.show_hidden),
            )

        if force_first_file:
            first_idx = self.next_tree_filter_result_entry_index(-1, 1)
            self.state.workspace.selected = first_idx if first_idx is not None else 0
        else:
            self.state.workspace.selected = 0
            matched_preferred = False
            if (
                self.state.filter.active
                and self.state.filter.query
                and self.state.filter.mode == "content"
                and previous_selected_hit_path is not None
            ):
                preserved_hit_idx = find_content_hit_index(
                    self.state.workspace.entries,
                    previous_selected_hit_path,
                    preferred_line=previous_selected_hit_line,
                    preferred_column=previous_selected_hit_column,
                    preferred_workspace_section=previous_selected_workspace_section,
                )
                if preserved_hit_idx is not None:
                    self.state.workspace.selected = preserved_hit_idx
                    matched_preferred = True

            if not matched_preferred:
                first_match_idx: int | None = None
                root_match_idx: int | None = None
                scoped_section_match_idx: int | None = None
                scoped_root_match_idx: int | None = None
                for idx, entry in enumerate(self.state.workspace.entries):
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
                    self.state.workspace.selected = chosen_idx
                    matched_preferred = True

            if not matched_preferred:
                if self.state.filter.active and self.state.filter.query:
                    first_idx = self.next_tree_filter_result_entry_index(-1, 1)
                    self.state.workspace.selected = first_idx if first_idx is not None else 0
                else:
                    self.state.workspace.selected = self.default_selected_index(prefer_files=bool(self.state.filter.query))

            if (
                self.state.filter.active
                and self.state.filter.query
                and self.state.filter.mode == "content"
                and not self.state.filter.editing
            ):
                coerced_idx = self.coerce_tree_filter_result_index(self.state.workspace.selected)
                self.state.workspace.selected = coerced_idx if coerced_idx is not None else 0

        if center_selection:
            rows = self.tree_view_rows()
            self.state.workspace.scroll = max(0, self.state.workspace.selected - max(1, rows // 2))

    def apply_tree_filter_query(
        self,
        query: str,
        preview_selection: bool = False,
        select_first_file: bool = False,
        debounce_prompt_row: bool = False,
    ) -> None:
        """Apply query text, rebuild results, and update loading indicator timing."""
        self.state.filter.query = query
        force_first_file = select_first_file and bool(query)
        preferred_path = None if force_first_file else self.state.workspace.current_path.resolve()
        suppress_prompt_row = bool(
            debounce_prompt_row
            and bool(query)
            and self.state.filter.mode == "content"
        )

        if self.state.filter.mode != "content":
            self.cancel_content_search()
            self.set_tree_filter_prompt_row_visible(True)
            self.loading_until = 0.0 if not query else time.monotonic() + 0.35
            self.rebuild_tree_entries(
                preferred_path=preferred_path,
                force_first_file=force_first_file,
            )
            if preview_selection:
                self.preview_selected_entry(force=True)
            self.state.interface.dirty = True
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
            self.state.filter.loading = False
            if preview_selection:
                self.preview_selected_entry(force=True)
            self.state.interface.dirty = True
            if self.on_tree_filter_state_change is not None:
                self.on_tree_filter_state_change()
            return

        match_limit = self.content_search_match_limit(query)
        request = self._content_request(query, match_limit)
        cached = self.search_service.cached_content(request)
        if cached is not None:
            self.cancel_content_search()
            if suppress_prompt_row:
                self._content_search_prompt_reveal_at = 0.0
                self._streaming_initial_rebuild_pending = False
                self.set_tree_filter_prompt_row_visible(False)
            else:
                self.set_tree_filter_prompt_row_visible(True)
            self.loading_until = 0.0
            matches_by_file = cached.as_dict()
            truncated = cached.truncated
            self.rebuild_tree_entries(
                preferred_path=preferred_path,
                force_first_file=force_first_file,
                content_matches_override=matches_by_file,
                content_truncated_override=truncated,
            )
            if preview_selection:
                self.preview_selected_entry(force=True)
            self.state.filter.loading = False
            self.state.interface.dirty = True
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
        self.state.interface.dirty = True
        if self.on_tree_filter_state_change is not None:
            self.on_tree_filter_state_change()
