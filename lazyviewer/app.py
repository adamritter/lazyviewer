from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

from .ansi import build_screen_lines
from .config import (
    load_left_pane_percent,
    load_show_hidden,
    save_left_pane_percent,
    save_show_hidden,
)
from .editor import launch_editor
from .fuzzy import (
    STRICT_SUBSTRING_ONLY_MIN_FILES,
    collect_project_file_labels,
    fuzzy_match_label_index,
    fuzzy_match_labels,
)
from .git_status import collect_git_status_overlay
from .highlight import colorize_source
from .input import read_key
from .preview import (
    DIR_PREVIEW_GROWTH_STEP,
    DIR_PREVIEW_HARD_MAX_ENTRIES,
    DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    build_rendered_for_path,
)
from .search import search_project_content_rg
from .render import help_panel_row_count, render_dual_page
from .state import AppState
from .symbols import collect_symbols
from .terminal import TerminalController
from .tree import (
    build_tree_entries,
    clamp_left_width,
    compute_left_width,
    filter_tree_entries_for_content_matches,
    filter_tree_entries_for_files,
    next_directory_entry_index,
    next_index_after_directory_subtree,
    next_file_entry_index,
    next_opened_directory_entry_index,
)

DOUBLE_CLICK_SECONDS = 0.35
PICKER_RESULT_LIMIT = 200
FILTER_CURSOR_BLINK_SECONDS = 0.5
TREE_FILTER_MATCH_LIMIT_1CHAR = 300
TREE_FILTER_MATCH_LIMIT_2CHAR = 1_000
TREE_FILTER_MATCH_LIMIT_3CHAR = 3_000
TREE_FILTER_MATCH_LIMIT_DEFAULT = 8_000
CONTENT_SEARCH_MATCH_LIMIT_1CHAR = 300
CONTENT_SEARCH_MATCH_LIMIT_2CHAR = 1_000
CONTENT_SEARCH_MATCH_LIMIT_3CHAR = 2_000
CONTENT_SEARCH_MATCH_LIMIT_DEFAULT = 4_000
CONTENT_SEARCH_FILE_LIMIT = 800
GIT_STATUS_REFRESH_SECONDS = 2.0


def run_pager(content: str, path: Path, style: str, no_color: bool, nopager: bool) -> None:
    if nopager or not os.isatty(sys.stdin.fileno()):
        rendered = content
        if not no_color and os.isatty(sys.stdout.fileno()):
            rendered = colorize_source(content, path, style)
        sys.stdout.write(content if no_color else rendered)
        return

    initial_path = path.resolve()
    current_path = initial_path
    tree_root = initial_path if initial_path.is_dir() else initial_path.parent
    expanded: set[Path] = {tree_root.resolve()}
    show_hidden = load_show_hidden()

    tree_entries = build_tree_entries(tree_root, expanded, show_hidden, skip_gitignored=show_hidden)
    selected_path = current_path if current_path.exists() else tree_root
    selected_idx = 0
    for idx, entry in enumerate(tree_entries):
        if entry.path.resolve() == selected_path.resolve():
            selected_idx = idx
            break

    term = shutil.get_terminal_size((80, 24))
    usable = max(1, term.lines - 1)
    saved_percent = load_left_pane_percent()
    if saved_percent is None:
        initial_left = compute_left_width(term.columns)
    else:
        initial_left = int((saved_percent / 100.0) * term.columns)
    left_width = clamp_left_width(term.columns, initial_left)
    right_width = max(1, term.columns - left_width - 2)
    initial_render = build_rendered_for_path(
        current_path,
        show_hidden,
        style,
        no_color,
        dir_max_entries=DIR_PREVIEW_INITIAL_MAX_ENTRIES,
        dir_skip_gitignored=show_hidden,
    )
    rendered = initial_render.text
    lines = build_screen_lines(rendered, right_width, wrap=False)
    max_start = max(0, len(lines) - usable)

    state = AppState(
        current_path=current_path,
        tree_root=tree_root,
        expanded=expanded,
        tree_render_expanded=set(expanded),
        show_hidden=show_hidden,
        tree_entries=tree_entries,
        selected_idx=selected_idx,
        rendered=rendered,
        lines=lines,
        start=0,
        tree_start=0,
        text_x=0,
        wrap_text=False,
        left_width=left_width,
        right_width=right_width,
        usable=usable,
        max_start=max_start,
        last_right_width=right_width,
        dir_preview_max_entries=DIR_PREVIEW_INITIAL_MAX_ENTRIES,
        dir_preview_truncated=initial_render.truncated,
        dir_preview_path=current_path if initial_render.is_directory else None,
    )

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    terminal = TerminalController(stdin_fd, stdout_fd)
    tree_filter_cursor_visible = True
    index_warmup_lock = threading.Lock()
    index_warmup_pending: tuple[Path, bool] | None = None
    index_warmup_running = False

    def index_warmup_worker() -> None:
        nonlocal index_warmup_pending, index_warmup_running
        while True:
            with index_warmup_lock:
                pending = index_warmup_pending
                index_warmup_pending = None
                if pending is None:
                    index_warmup_running = False
                    return

            root, show_hidden_value = pending
            try:
                collect_project_file_labels(
                    root,
                    show_hidden_value,
                    skip_gitignored=show_hidden_value,
                )
            except Exception:
                # Warming is best-effort; foreground path still loads synchronously if needed.
                pass

    def schedule_tree_filter_index_warmup(
        root: Path | None = None,
        show_hidden_value: bool | None = None,
    ) -> None:
        nonlocal index_warmup_pending, index_warmup_running
        if root is None:
            root = state.tree_root
        if show_hidden_value is None:
            show_hidden_value = state.show_hidden

        with index_warmup_lock:
            index_warmup_pending = (root.resolve(), show_hidden_value)
            if index_warmup_running:
                return
            index_warmup_running = True

        worker = threading.Thread(
            target=index_warmup_worker,
            name="lazyviewer-file-index",
            daemon=True,
        )
        worker.start()

    def effective_text_width(columns: int | None = None) -> int:
        if columns is None:
            columns = shutil.get_terminal_size((80, 24)).columns
        if state.browser_visible:
            return max(1, columns - state.left_width - 2)
        return max(1, columns - 1)

    def visible_content_rows() -> int:
        help_rows = help_panel_row_count(state.usable, state.show_help)
        return max(1, state.usable - help_rows)

    def rebuild_screen_lines(
        columns: int | None = None,
        preserve_scroll: bool = True,
    ) -> None:
        state.lines = build_screen_lines(
            state.rendered,
            effective_text_width(columns),
            wrap=state.wrap_text,
        )
        state.max_start = max(0, len(state.lines) - visible_content_rows())
        if preserve_scroll:
            state.start = max(0, min(state.start, state.max_start))
        else:
            state.start = 0
        if state.wrap_text:
            state.text_x = 0

    def refresh_git_status_overlay(force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - state.git_status_last_refresh) < GIT_STATUS_REFRESH_SECONDS:
            return

        previous = state.git_status_overlay
        state.git_status_overlay = collect_git_status_overlay(state.tree_root)
        state.git_status_last_refresh = time.monotonic()
        if state.git_status_overlay != previous:
            state.dirty = True

    def refresh_rendered_for_current_path(
        reset_scroll: bool = True,
        reset_dir_budget: bool = False,
    ) -> None:
        resolved_target = state.current_path.resolve()
        is_dir_target = resolved_target.is_dir()
        if is_dir_target:
            if reset_dir_budget or state.dir_preview_path != resolved_target:
                state.dir_preview_max_entries = DIR_PREVIEW_INITIAL_MAX_ENTRIES
            dir_limit = state.dir_preview_max_entries
        else:
            dir_limit = DIR_PREVIEW_INITIAL_MAX_ENTRIES

        rendered_for_path = build_rendered_for_path(
            state.current_path,
            state.show_hidden,
            style,
            no_color,
            dir_max_entries=dir_limit,
            dir_skip_gitignored=state.show_hidden,
        )
        state.rendered = rendered_for_path.text
        rebuild_screen_lines(preserve_scroll=not reset_scroll)
        state.dir_preview_truncated = rendered_for_path.truncated
        state.dir_preview_path = resolved_target if rendered_for_path.is_directory else None
        if reset_scroll:
            state.text_x = 0

    def preview_selected_entry(force: bool = False) -> None:
        if not state.tree_entries:
            return
        entry = state.tree_entries[state.selected_idx]
        selected_target = entry.path.resolve()
        if entry.kind == "search_hit":
            if force or selected_target != state.current_path.resolve():
                state.current_path = selected_target
                refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
            if entry.line is not None:
                jump_to_line(max(0, entry.line - 1))
            return
        if not force and selected_target == state.current_path.resolve():
            return
        state.current_path = selected_target
        refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)

    def maybe_grow_directory_preview() -> bool:
        if state.dir_preview_path is None or not state.dir_preview_truncated:
            return False
        if state.current_path.resolve() != state.dir_preview_path:
            return False
        if state.dir_preview_max_entries >= DIR_PREVIEW_HARD_MAX_ENTRIES:
            return False

        # Only grow when the user is effectively at the end of the current preview.
        near_end_threshold = max(1, visible_content_rows() // 3)
        if state.start < max(0, state.max_start - near_end_threshold):
            return False

        previous_line_count = len(state.lines)
        state.dir_preview_max_entries = min(
            DIR_PREVIEW_HARD_MAX_ENTRIES,
            state.dir_preview_max_entries + DIR_PREVIEW_GROWTH_STEP,
        )
        refresh_rendered_for_current_path(reset_scroll=False, reset_dir_budget=False)
        return len(state.lines) > previous_line_count

    def refresh_tree_filter_file_index() -> None:
        root = state.tree_root.resolve()
        if state.picker_files_root == root and state.picker_files_show_hidden == state.show_hidden:
            return
        state.picker_file_labels = collect_project_file_labels(
            root,
            state.show_hidden,
            skip_gitignored=state.show_hidden,
        )
        state.picker_file_labels_folded = []
        state.picker_files_root = root
        state.picker_files_show_hidden = state.show_hidden

    def default_selected_index(prefer_files: bool = False) -> int:
        if not state.tree_entries:
            return 0
        if prefer_files:
            for idx, entry in enumerate(state.tree_entries):
                if not entry.is_dir:
                    return idx
        if len(state.tree_entries) > 1:
            return 1
        return 0

    def tree_filter_prompt_prefix() -> str:
        if state.tree_filter_mode == "content":
            return "/>"
        return "p>"

    def tree_filter_placeholder() -> str:
        if state.tree_filter_mode == "content":
            return "type to search content"
        return "type to filter files"

    def tree_view_rows() -> int:
        rows = visible_content_rows()
        if state.tree_filter_active and not state.picker_active:
            return max(1, rows - 1)
        return rows

    def tree_filter_match_limit(query: str) -> int:
        if len(query) <= 1:
            return TREE_FILTER_MATCH_LIMIT_1CHAR
        if len(query) == 2:
            return TREE_FILTER_MATCH_LIMIT_2CHAR
        if len(query) == 3:
            return TREE_FILTER_MATCH_LIMIT_3CHAR
        return TREE_FILTER_MATCH_LIMIT_DEFAULT

    def content_search_match_limit(query: str) -> int:
        if len(query) <= 1:
            return CONTENT_SEARCH_MATCH_LIMIT_1CHAR
        if len(query) == 2:
            return CONTENT_SEARCH_MATCH_LIMIT_2CHAR
        if len(query) == 3:
            return CONTENT_SEARCH_MATCH_LIMIT_3CHAR
        return CONTENT_SEARCH_MATCH_LIMIT_DEFAULT

    def next_content_hit_entry_index(selected_idx: int, direction: int) -> int | None:
        if not state.tree_entries or direction == 0:
            return None
        step = 1 if direction > 0 else -1
        idx = selected_idx + step
        while 0 <= idx < len(state.tree_entries):
            if state.tree_entries[idx].kind == "search_hit":
                return idx
            idx += step
        return None

    def first_tree_filter_result_index() -> int | None:
        if state.tree_filter_mode == "content":
            return next_content_hit_entry_index(-1, 1)
        return next_file_entry_index(state.tree_entries, -1, 1)

    def rebuild_tree_entries(
        preferred_path: Path | None = None,
        center_selection: bool = False,
        force_first_file: bool = False,
    ) -> None:
        if preferred_path is None:
            if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries):
                preferred_path = state.tree_entries[state.selected_idx].path.resolve()
            else:
                preferred_path = state.current_path.resolve()

        if state.tree_filter_active and state.tree_filter_query:
            if state.tree_filter_mode == "content":
                match_limit = content_search_match_limit(state.tree_filter_query)
                matches_by_file, truncated, _error = search_project_content_rg(
                    state.tree_root,
                    state.tree_filter_query,
                    state.show_hidden,
                    skip_gitignored=state.show_hidden,
                    max_matches=max(1, match_limit),
                    max_files=CONTENT_SEARCH_FILE_LIMIT,
                )
                state.tree_filter_match_count = sum(len(items) for items in matches_by_file.values())
                state.tree_filter_truncated = truncated
                state.tree_entries, state.tree_render_expanded = filter_tree_entries_for_content_matches(
                    state.tree_root,
                    state.expanded,
                    matches_by_file,
                )
            else:
                refresh_tree_filter_file_index()
                match_limit = min(len(state.picker_file_labels), tree_filter_match_limit(state.tree_filter_query))
                labels_folded: list[str] | None = None
                if len(state.picker_file_labels) < STRICT_SUBSTRING_ONLY_MIN_FILES:
                    if len(state.picker_file_labels_folded) != len(state.picker_file_labels):
                        state.picker_file_labels_folded = [label.casefold() for label in state.picker_file_labels]
                    labels_folded = state.picker_file_labels_folded
                matched = fuzzy_match_label_index(
                    state.tree_filter_query,
                    state.picker_file_labels,
                    labels_folded=labels_folded,
                    limit=max(1, match_limit),
                )
                root = state.tree_root.resolve()
                matched_paths = [root / label for _, label, _ in matched]
                state.tree_filter_match_count = len(matched_paths)
                state.tree_filter_truncated = False
                state.tree_entries, state.tree_render_expanded = filter_tree_entries_for_files(
                    state.tree_root,
                    state.expanded,
                    state.show_hidden,
                    matched_paths,
                    skip_gitignored=state.show_hidden,
                )
        else:
            state.tree_filter_match_count = 0
            state.tree_filter_truncated = False
            state.tree_render_expanded = set(state.expanded)
            state.tree_entries = build_tree_entries(
                state.tree_root,
                state.expanded,
                state.show_hidden,
                skip_gitignored=state.show_hidden,
            )

        if force_first_file:
            first_idx = first_tree_filter_result_index()
            state.selected_idx = first_idx if first_idx is not None else 0
        else:
            preferred_target = preferred_path.resolve()
            state.selected_idx = 0
            for idx, entry in enumerate(state.tree_entries):
                if entry.kind == "search_hit":
                    continue
                if entry.path.resolve() == preferred_target:
                    state.selected_idx = idx
                    break
            else:
                if state.tree_filter_active and state.tree_filter_query:
                    first_idx = first_tree_filter_result_index()
                    state.selected_idx = first_idx if first_idx is not None else 0
                else:
                    state.selected_idx = default_selected_index(prefer_files=bool(state.tree_filter_query))

        if center_selection:
            rows = tree_view_rows()
            state.tree_start = max(0, state.selected_idx - max(1, rows // 2))

    def apply_tree_filter_query(
        query: str,
        preview_selection: bool = False,
        select_first_file: bool = False,
    ) -> None:
        state.tree_filter_query = query
        force_first_file = select_first_file and bool(query)
        preferred_path = None if force_first_file else state.current_path.resolve()
        rebuild_tree_entries(
            preferred_path=preferred_path,
            force_first_file=force_first_file,
        )
        if preview_selection:
            preview_selected_entry(force=True)
        state.dirty = True

    def open_tree_filter(mode: str = "files") -> None:
        was_active = state.tree_filter_active
        previous_mode = state.tree_filter_mode
        if not state.tree_filter_active:
            state.tree_filter_prev_browser_visible = state.browser_visible
        was_browser_visible = state.browser_visible
        state.browser_visible = True
        if state.wrap_text and not was_browser_visible:
            rebuild_screen_lines()
        state.tree_filter_active = True
        state.tree_filter_mode = mode
        state.tree_filter_editing = True
        state.tree_filter_query = ""
        state.tree_filter_match_count = 0
        state.tree_filter_truncated = False
        if was_active and previous_mode != mode:
            rebuild_tree_entries(preferred_path=state.current_path.resolve())
        state.dirty = True

    def close_tree_filter(clear_query: bool = True) -> None:
        previous_browser_visible = state.tree_filter_prev_browser_visible
        state.tree_filter_active = False
        state.tree_filter_editing = False
        state.tree_filter_mode = "files"
        if clear_query:
            state.tree_filter_query = ""
            state.tree_filter_truncated = False
        state.tree_filter_prev_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = state.browser_visible != previous_browser_visible
            state.browser_visible = previous_browser_visible
            if state.wrap_text and browser_visibility_changed:
                rebuild_screen_lines()
        rebuild_tree_entries(preferred_path=state.current_path.resolve())
        state.dirty = True

    def activate_tree_filter_selection() -> None:
        if not state.tree_entries:
            close_tree_filter(clear_query=True)
            return

        entry = state.tree_entries[state.selected_idx]
        if entry.is_dir:
            if state.tree_filter_mode == "content":
                candidate_idx = next_content_hit_entry_index(state.selected_idx, 1)
            else:
                candidate_idx = next_file_entry_index(state.tree_entries, state.selected_idx, 1)
            if candidate_idx is None:
                if state.tree_filter_mode == "content":
                    candidate_idx = next_content_hit_entry_index(state.selected_idx, -1)
                else:
                    candidate_idx = next_file_entry_index(state.tree_entries, state.selected_idx, -1)
            if candidate_idx is None:
                close_tree_filter(clear_query=True)
                return
            state.selected_idx = candidate_idx
            entry = state.tree_entries[state.selected_idx]

        selected_path = entry.path.resolve()
        selected_line = entry.line if entry.kind == "search_hit" else None
        close_tree_filter(clear_query=True)
        jump_to_path(selected_path)
        if selected_line is not None:
            jump_to_line(max(0, selected_line - 1))
        state.dirty = True

    def refresh_symbol_picker_matches(reset_selection: bool = False) -> None:
        matched = fuzzy_match_labels(
            state.picker_query,
            state.picker_symbol_labels,
            limit=PICKER_RESULT_LIMIT,
        )
        state.picker_matches = []
        state.picker_match_labels = [label for _, label, _ in matched]
        state.picker_match_lines = [state.picker_symbol_lines[idx] for idx, _, _ in matched]
        if state.picker_match_labels:
            state.picker_message = ""
        elif not state.picker_message:
            state.picker_message = " no matching symbols"
        state.picker_selected = 0 if reset_selection else max(
            0,
            min(state.picker_selected, max(0, len(state.picker_match_labels) - 1)),
        )
        if reset_selection or not state.picker_match_labels:
            state.picker_list_start = 0

    def resolve_symbol_target() -> Path | None:
        if state.current_path.is_file():
            return state.current_path.resolve()
        if not state.tree_entries:
            return None
        entry = state.tree_entries[state.selected_idx]
        if entry.is_dir or not entry.path.is_file():
            return None
        return entry.path.resolve()

    def open_symbol_picker() -> None:
        if not state.picker_active:
            state.picker_prev_browser_visible = state.browser_visible
        state.picker_active = True
        state.picker_mode = "symbols"
        state.picker_focus = "query"
        state.picker_message = ""
        state.picker_query = ""
        state.picker_selected = 0
        state.picker_list_start = 0
        state.picker_matches = []
        state.picker_match_labels = []
        state.picker_match_lines = []
        was_browser_visible = state.browser_visible
        state.browser_visible = True
        if state.wrap_text and not was_browser_visible:
            rebuild_screen_lines()

        target = resolve_symbol_target()
        state.picker_symbol_file = target
        state.picker_symbol_labels = []
        state.picker_symbol_lines = []
        if target is None:
            state.picker_message = " no file selected"
            state.dirty = True
            return

        symbols, error = collect_symbols(target)
        if error:
            state.picker_message = f" {error}"
            state.dirty = True
            return

        state.picker_symbol_labels = [symbol.label for symbol in symbols]
        state.picker_symbol_lines = [symbol.line for symbol in symbols]
        if not state.picker_symbol_labels:
            state.picker_message = " no functions/classes/imports found"
            state.dirty = True
            return

        refresh_symbol_picker_matches(reset_selection=True)
        state.dirty = True

    def close_picker(reset_query: bool = True) -> None:
        previous_browser_visible = state.picker_prev_browser_visible
        state.picker_active = False
        if reset_query:
            state.picker_query = ""
        state.picker_mode = "symbols"
        state.picker_focus = "query"
        state.picker_message = ""
        state.picker_selected = 0
        state.picker_list_start = 0
        state.picker_matches = []
        state.picker_match_labels = []
        state.picker_match_lines = []
        state.picker_symbol_file = None
        state.picker_symbol_labels = []
        state.picker_symbol_lines = []
        state.picker_prev_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = state.browser_visible != previous_browser_visible
            state.browser_visible = previous_browser_visible
            if state.wrap_text and browser_visibility_changed:
                rebuild_screen_lines()
        state.dirty = True

    def activate_picker_selection() -> None:
        if state.picker_mode == "symbols" and state.picker_match_lines:
            selected_line = state.picker_match_lines[state.picker_selected]
            symbol_file = state.picker_symbol_file
            close_picker()
            if symbol_file is not None and symbol_file.resolve() != state.current_path.resolve():
                jump_to_path(symbol_file.resolve())
            jump_to_line(selected_line)

    def reveal_path_in_tree(target: Path) -> None:
        target = target.resolve()
        if target != state.tree_root:
            parent = target.parent
            while True:
                resolved = parent.resolve()
                if resolved == state.tree_root:
                    break
                state.expanded.add(resolved)
                if resolved.parent == resolved:
                    break
                parent = resolved.parent
        state.expanded.add(state.tree_root)
        rebuild_tree_entries(preferred_path=target, center_selection=True)

    def jump_to_path(target: Path) -> None:
        target = target.resolve()
        reveal_path_in_tree(target)
        state.current_path = target
        refresh_rendered_for_current_path()

    def jump_to_line(line_number: int) -> None:
        state.max_start = max(0, len(state.lines) - visible_content_rows())
        anchor = max(0, min(line_number, state.max_start))
        centered = max(0, anchor - max(1, visible_content_rows() // 3))
        state.start = max(0, min(centered, state.max_start))
        state.text_x = 0

    schedule_tree_filter_index_warmup()
    refresh_git_status_overlay(force=True)

    with terminal.raw_mode():
        while True:
            term = shutil.get_terminal_size((80, 24))
            state.usable = max(1, term.lines - 1)
            state.left_width = clamp_left_width(term.columns, state.left_width)
            state.right_width = max(1, term.columns - state.left_width - 2)
            if state.right_width != state.last_right_width:
                state.last_right_width = state.right_width
                rebuild_screen_lines(columns=term.columns)
                state.dirty = True
            state.max_start = max(0, len(state.lines) - visible_content_rows())

            prev_tree_start = state.tree_start
            visible_tree_rows = tree_view_rows()
            if state.selected_idx < state.tree_start:
                state.tree_start = state.selected_idx
            elif state.selected_idx >= state.tree_start + visible_tree_rows:
                state.tree_start = state.selected_idx - visible_tree_rows + 1
            state.tree_start = max(
                0,
                min(state.tree_start, max(0, len(state.tree_entries) - visible_tree_rows)),
            )
            if state.tree_start != prev_tree_start:
                state.dirty = True

            if state.picker_active and state.picker_mode == "symbols":
                picker_rows = max(1, visible_content_rows() - 1)
                max_picker_start = max(0, len(state.picker_match_labels) - picker_rows)
                prev_picker_start = state.picker_list_start
                if state.picker_selected < state.picker_list_start:
                    state.picker_list_start = state.picker_selected
                elif state.picker_selected >= state.picker_list_start + picker_rows:
                    state.picker_list_start = state.picker_selected - picker_rows + 1
                state.picker_list_start = max(0, min(state.picker_list_start, max_picker_start))
                if state.picker_list_start != prev_picker_start:
                    state.dirty = True

            blinking_filter = (
                state.tree_filter_active
                and state.tree_filter_editing
                and not state.picker_active
                and state.browser_visible
            )
            if blinking_filter:
                blink_phase = (int(time.monotonic() / FILTER_CURSOR_BLINK_SECONDS) % 2) == 0
                if blink_phase != tree_filter_cursor_visible:
                    tree_filter_cursor_visible = blink_phase
                    state.dirty = True
            elif not tree_filter_cursor_visible:
                tree_filter_cursor_visible = True
                state.dirty = True

            refresh_git_status_overlay()

            if state.dirty:
                render_dual_page(
                    state.lines,
                    state.start,
                    state.tree_entries,
                    state.tree_start,
                    state.selected_idx,
                    state.usable,
                    state.current_path,
                    state.tree_root,
                    state.tree_render_expanded,
                    term.columns,
                    state.left_width,
                    state.text_x,
                    state.wrap_text,
                    state.browser_visible,
                    state.show_hidden,
                    state.show_help,
                    state.tree_filter_active,
                    state.tree_filter_query,
                    state.tree_filter_editing,
                    tree_filter_cursor_visible,
                    state.tree_filter_match_count,
                    tree_filter_prompt_prefix(),
                    tree_filter_placeholder(),
                    state.picker_active,
                    state.picker_mode,
                    state.picker_query,
                    state.picker_match_labels,
                    state.picker_selected,
                    state.picker_focus,
                    state.picker_list_start,
                    state.picker_message,
                    git_status_overlay=state.git_status_overlay,
                    tree_search_query=(
                        state.tree_filter_query
                        if state.tree_filter_active and state.tree_filter_mode == "content"
                        else ""
                    ),
                    text_search_query=(
                        state.tree_filter_query
                        if state.tree_filter_active and state.tree_filter_mode == "content"
                        else ""
                    ),
                )
                state.dirty = False

            key = read_key(stdin_fd, timeout_ms=120)
            if key == "":
                continue
            if state.skip_next_lf and key == "ENTER_LF":
                state.skip_next_lf = False
                continue

            if key == "ENTER_CR":
                key = "ENTER"
                state.skip_next_lf = True
            elif key == "ENTER_LF":
                if state.tree_filter_active and state.tree_filter_editing:
                    key = "CTRL_J"
                else:
                    key = "ENTER"
                state.skip_next_lf = False
            else:
                state.skip_next_lf = False

            if key == "SHIFT_LEFT":
                prev_left = state.left_width
                state.left_width = clamp_left_width(term.columns, state.left_width - 2)
                if state.left_width != prev_left:
                    save_left_pane_percent(term.columns, state.left_width)
                    state.right_width = max(1, term.columns - state.left_width - 2)
                    if state.right_width != state.last_right_width:
                        state.last_right_width = state.right_width
                        rebuild_screen_lines(columns=term.columns)
                    state.dirty = True
                continue
            if key == "SHIFT_RIGHT":
                prev_left = state.left_width
                state.left_width = clamp_left_width(term.columns, state.left_width + 2)
                if state.left_width != prev_left:
                    save_left_pane_percent(term.columns, state.left_width)
                    state.right_width = max(1, term.columns - state.left_width - 2)
                    if state.right_width != state.last_right_width:
                        state.last_right_width = state.right_width
                        rebuild_screen_lines(columns=term.columns)
                    state.dirty = True
                continue

            if key == "CTRL_P":
                state.count_buffer = ""
                if state.tree_filter_active:
                    if state.tree_filter_mode == "files" and state.tree_filter_editing:
                        close_tree_filter(clear_query=True)
                    elif state.tree_filter_mode != "files":
                        open_tree_filter(mode="files")
                    else:
                        state.tree_filter_editing = True
                        state.dirty = True
                else:
                    open_tree_filter(mode="files")
                continue

            if key == "/" and not state.picker_active and not (state.tree_filter_active and state.tree_filter_editing):
                state.count_buffer = ""
                if state.tree_filter_active:
                    if state.tree_filter_mode == "content" and state.tree_filter_editing:
                        close_tree_filter(clear_query=True)
                    elif state.tree_filter_mode != "content":
                        open_tree_filter(mode="content")
                    else:
                        state.tree_filter_editing = True
                        state.dirty = True
                else:
                    open_tree_filter(mode="content")
                continue

            if state.picker_active:
                if key == "ESC" or key == "\x03":
                    close_picker()
                    continue
                if key == "TAB":
                    state.picker_focus = "tree" if state.picker_focus == "query" else "query"
                    state.dirty = True
                    continue

                if state.picker_focus == "query":
                    if key == "ENTER":
                        state.picker_focus = "tree"
                        state.dirty = True
                        continue
                    if key == "BACKSPACE":
                        if state.picker_query:
                            state.picker_query = state.picker_query[:-1]
                            refresh_symbol_picker_matches(reset_selection=True)
                            state.dirty = True
                        continue
                    if len(key) == 1 and key.isprintable():
                        state.picker_query += key
                        refresh_symbol_picker_matches(reset_selection=True)
                        state.dirty = True
                    continue

                if key == "ENTER" or key.lower() == "l":
                    activate_picker_selection()
                    state.dirty = True
                    continue
                if key == "UP" or key.lower() == "k":
                    if state.picker_match_labels:
                        state.picker_selected = max(0, state.picker_selected - 1)
                        state.dirty = True
                    continue
                if key == "DOWN" or key.lower() == "j":
                    if state.picker_match_labels:
                        state.picker_selected = min(len(state.picker_match_labels) - 1, state.picker_selected + 1)
                        state.dirty = True
                    continue
                if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
                    direction = -1 if key.startswith("MOUSE_WHEEL_UP:") else 1
                    parts = key.split(":")
                    col: int | None = None
                    if len(parts) >= 3:
                        try:
                            col = int(parts[1])
                        except Exception:
                            col = None
                    if state.browser_visible and col is not None and col <= state.left_width:
                        if state.picker_match_labels:
                            prev_selected = state.picker_selected
                            state.picker_selected = max(
                                0,
                                min(len(state.picker_match_labels) - 1, state.picker_selected + direction),
                            )
                            if state.picker_selected != prev_selected:
                                state.dirty = True
                    else:
                        prev_start = state.start
                        state.start += direction * 3
                        state.start = max(0, min(state.start, state.max_start))
                        if state.start != prev_start:
                            state.dirty = True
                    continue
                if key.startswith("MOUSE_LEFT_DOWN:"):
                    parts = key.split(":")
                    if len(parts) >= 3:
                        try:
                            col = int(parts[1])
                            row = int(parts[2])
                        except Exception:
                            col = None
                            row = None
                        if (
                            state.browser_visible
                            and col is not None
                            and row is not None
                            and 1 <= row <= visible_content_rows()
                            and col <= state.left_width
                        ):
                            if row == 1:
                                state.picker_focus = "query"
                                state.dirty = True
                            else:
                                clicked_idx = state.picker_list_start + (row - 2)
                                if 0 <= clicked_idx < len(state.picker_match_labels):
                                    prev_selected = state.picker_selected
                                    state.picker_selected = clicked_idx
                                    if state.picker_selected != prev_selected:
                                        state.dirty = True
                                    now = time.monotonic()
                                    is_double = (
                                        clicked_idx == state.last_click_idx
                                        and (now - state.last_click_time) <= DOUBLE_CLICK_SECONDS
                                    )
                                    state.last_click_idx = clicked_idx
                                    state.last_click_time = now
                                    if is_double:
                                        activate_picker_selection()
                                        state.dirty = True
                    continue
                continue

            if state.tree_filter_active and state.tree_filter_editing:
                if key == "ESC":
                    close_tree_filter(clear_query=True)
                    continue
                if key == "ENTER":
                    activate_tree_filter_selection()
                    continue
                if key == "TAB":
                    state.tree_filter_editing = False
                    state.dirty = True
                    continue
                if key == "UP" or key == "CTRL_K":
                    if state.tree_filter_mode == "content":
                        target_idx = next_content_hit_entry_index(state.selected_idx, -1)
                    else:
                        target_idx = next_file_entry_index(state.tree_entries, state.selected_idx, -1)
                    if target_idx is not None:
                        prev_selected = state.selected_idx
                        state.selected_idx = target_idx
                        preview_selected_entry()
                        if state.selected_idx != prev_selected:
                            state.dirty = True
                    continue
                if key == "DOWN" or key == "CTRL_J":
                    if state.tree_filter_mode == "content":
                        target_idx = next_content_hit_entry_index(state.selected_idx, 1)
                    else:
                        target_idx = next_file_entry_index(state.tree_entries, state.selected_idx, 1)
                    if target_idx is not None:
                        prev_selected = state.selected_idx
                        state.selected_idx = target_idx
                        preview_selected_entry()
                        if state.selected_idx != prev_selected:
                            state.dirty = True
                    continue
                if key == "BACKSPACE":
                    if state.tree_filter_query:
                        apply_tree_filter_query(
                            state.tree_filter_query[:-1],
                            preview_selection=True,
                            select_first_file=True,
                        )
                    continue
                if key == "CTRL_U":
                    if state.tree_filter_query:
                        apply_tree_filter_query(
                            "",
                            preview_selection=True,
                            select_first_file=True,
                        )
                    continue
                if len(key) == 1 and key.isprintable():
                    apply_tree_filter_query(
                        state.tree_filter_query + key,
                        preview_selection=True,
                        select_first_file=True,
                    )
                    continue
                continue

            if state.tree_filter_active and not state.tree_filter_editing:
                if key == "TAB":
                    state.tree_filter_editing = True
                    state.dirty = True
                    continue
                if key == "ENTER":
                    activate_tree_filter_selection()
                    continue
                if key == "ESC":
                    close_tree_filter(clear_query=True)
                    continue

            if key.lower() == "s" and not state.picker_active:
                state.count_buffer = ""
                open_symbol_picker()
                continue

            if key.isdigit():
                state.count_buffer += key
                continue

            count = int(state.count_buffer) if state.count_buffer else None
            state.count_buffer = ""
            if key == "?":
                state.show_help = not state.show_help
                rebuild_screen_lines(columns=term.columns)
                state.dirty = True
                continue
            if key == "CTRL_U" or key == "CTRL_D":
                if state.browser_visible and state.tree_entries:
                    direction = -1 if key == "CTRL_U" else 1
                    jump_steps = 1 if count is None else max(1, min(10, count))

                    def parent_directory_index(from_idx: int) -> int | None:
                        current_depth = state.tree_entries[from_idx].depth
                        idx = from_idx - 1
                        while idx >= 0:
                            candidate = state.tree_entries[idx]
                            if candidate.is_dir and candidate.depth < current_depth:
                                return idx
                            idx -= 1
                        return None

                    def smart_directory_jump(from_idx: int, jump_direction: int) -> int | None:
                        if jump_direction < 0:
                            prev_opened = next_opened_directory_entry_index(
                                state.tree_entries,
                                from_idx,
                                -1,
                                state.expanded,
                            )
                            if prev_opened is not None:
                                return prev_opened
                            return parent_directory_index(from_idx)

                        current_entry = state.tree_entries[from_idx]
                        if current_entry.is_dir and current_entry.path.resolve() in state.expanded:
                            after_current = next_index_after_directory_subtree(state.tree_entries, from_idx)
                            if after_current is not None:
                                return after_current

                        next_opened = next_opened_directory_entry_index(
                            state.tree_entries,
                            from_idx,
                            1,
                            state.expanded,
                        )
                        if next_opened is not None:
                            after_next_opened = next_index_after_directory_subtree(state.tree_entries, next_opened)
                            if after_next_opened is not None:
                                return after_next_opened
                            return next_opened

                        return next_directory_entry_index(state.tree_entries, from_idx, 1)

                    target_idx = state.selected_idx
                    moved = 0
                    while moved < jump_steps:
                        next_idx = smart_directory_jump(target_idx, direction)
                        if next_idx is None:
                            break
                        target_idx = next_idx
                        moved += 1
                    if moved > 0:
                        prev_selected = state.selected_idx
                        state.selected_idx = target_idx
                        preview_selected_entry()
                        if state.selected_idx != prev_selected:
                            state.dirty = True
                continue
            if key == "R":
                old_root = state.tree_root.resolve()
                parent_root = old_root.parent.resolve()
                if parent_root != old_root:
                    state.tree_root = parent_root
                    state.expanded = {state.tree_root, old_root}
                    rebuild_tree_entries(preferred_path=old_root, center_selection=True)
                    preview_selected_entry(force=True)
                    schedule_tree_filter_index_warmup()
                    refresh_git_status_overlay(force=True)
                    state.dirty = True
                continue
            if key == "r":
                selected_entry = (
                    state.tree_entries[state.selected_idx]
                    if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries)
                    else None
                )
                if selected_entry is not None:
                    selected_target = selected_entry.path.resolve()
                    target_root = selected_target if selected_entry.is_dir else selected_target.parent.resolve()
                else:
                    selected_target = state.current_path.resolve()
                    target_root = selected_target if selected_target.is_dir() else selected_target.parent.resolve()

                old_root = state.tree_root.resolve()
                if target_root != old_root:
                    state.tree_root = target_root
                    state.expanded = {state.tree_root}
                    rebuild_tree_entries(preferred_path=selected_target, center_selection=True)
                    preview_selected_entry(force=True)
                    schedule_tree_filter_index_warmup()
                    refresh_git_status_overlay(force=True)
                    state.dirty = True
                continue
            if key == ".":
                state.show_hidden = not state.show_hidden
                save_show_hidden(state.show_hidden)
                selected_path = (
                    state.tree_entries[state.selected_idx].path.resolve() if state.tree_entries else state.tree_root
                )
                rebuild_tree_entries(preferred_path=selected_path)
                preview_selected_entry(force=True)
                schedule_tree_filter_index_warmup()
                state.dirty = True
                continue
            if key.lower() == "t":
                state.browser_visible = not state.browser_visible
                if state.wrap_text:
                    rebuild_screen_lines(columns=term.columns)
                state.dirty = True
                continue
            if key.lower() == "w":
                state.wrap_text = not state.wrap_text
                if state.wrap_text:
                    state.text_x = 0
                rebuild_screen_lines(columns=term.columns)
                state.dirty = True
                continue
            if key.lower() == "e":
                edit_target: Path | None = None
                if state.browser_visible and state.tree_entries:
                    selected_entry = state.tree_entries[state.selected_idx]
                    if not selected_entry.is_dir and selected_entry.path.is_file():
                        edit_target = selected_entry.path.resolve()
                if edit_target is None and state.current_path.is_file():
                    edit_target = state.current_path.resolve()
                if edit_target is None:
                    state.rendered = "\033[31m<cannot edit a directory>\033[0m"
                    rebuild_screen_lines(columns=term.columns, preserve_scroll=False)
                    state.text_x = 0
                    state.dirty = True
                    continue

                error = launch_editor(edit_target, terminal.disable_tui_mode, terminal.enable_tui_mode)
                state.current_path = edit_target
                if error is None:
                    refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
                    refresh_git_status_overlay(force=True)
                else:
                    state.rendered = f"\033[31m{error}\033[0m"
                    rebuild_screen_lines(columns=term.columns, preserve_scroll=False)
                    state.text_x = 0
                    state.dir_preview_path = None
                    state.dir_preview_truncated = False
                state.dirty = True
                continue
            if key.lower() == "q" or key == "\x03":
                break
            if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
                direction = -1 if key.startswith("MOUSE_WHEEL_UP:") else 1
                parts = key.split(":")
                col: int | None = None
                if len(parts) >= 3:
                    try:
                        col = int(parts[1])
                    except Exception:
                        col = None
                if state.browser_visible and col is not None and col <= state.left_width:
                    prev_selected = state.selected_idx
                    state.selected_idx = max(0, min(len(state.tree_entries) - 1, state.selected_idx + direction))
                    preview_selected_entry()
                    if state.selected_idx != prev_selected:
                        state.dirty = True
                else:
                    prev_start = state.start
                    state.start += direction * 3
                    state.start = max(0, min(state.start, state.max_start))
                    grew_preview = direction > 0 and maybe_grow_directory_preview()
                    if state.start != prev_start or grew_preview:
                        state.dirty = True
                continue
            if key.startswith("MOUSE_LEFT_DOWN:"):
                parts = key.split(":")
                if len(parts) >= 3:
                    try:
                        col = int(parts[1])
                        row = int(parts[2])
                    except Exception:
                        col = None
                        row = None
                    if (
                        state.browser_visible
                        and col is not None
                        and row is not None
                        and 1 <= row <= visible_content_rows()
                        and col <= state.left_width
                    ):
                        query_row_visible = state.tree_filter_active
                        if query_row_visible and row == 1:
                            state.tree_filter_editing = True
                            state.dirty = True
                            continue

                        clicked_idx = state.tree_start + (row - 1 - (1 if query_row_visible else 0))
                        if 0 <= clicked_idx < len(state.tree_entries):
                            prev_selected = state.selected_idx
                            state.selected_idx = clicked_idx
                            preview_selected_entry()
                            if state.selected_idx != prev_selected:
                                state.dirty = True
                            now = time.monotonic()
                            is_double = (
                                clicked_idx == state.last_click_idx
                                and (now - state.last_click_time) <= DOUBLE_CLICK_SECONDS
                            )
                            state.last_click_idx = clicked_idx
                            state.last_click_time = now
                            if is_double:
                                entry = state.tree_entries[state.selected_idx]
                                if entry.is_dir:
                                    resolved = entry.path.resolve()
                                    if resolved in state.expanded:
                                        state.expanded.remove(resolved)
                                    else:
                                        state.expanded.add(resolved)
                                    rebuild_tree_entries(preferred_path=resolved)
                                    state.dirty = True
                                else:
                                    state.current_path = entry.path.resolve()
                                    refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
                                    state.dirty = True
                continue
            if state.browser_visible and key.lower() == "j":
                prev_selected = state.selected_idx
                state.selected_idx = min(len(state.tree_entries) - 1, state.selected_idx + 1)
                preview_selected_entry()
                if state.selected_idx != prev_selected:
                    state.dirty = True
                continue
            if state.browser_visible and key.lower() == "k":
                prev_selected = state.selected_idx
                state.selected_idx = max(0, state.selected_idx - 1)
                preview_selected_entry()
                if state.selected_idx != prev_selected:
                    state.dirty = True
                continue
            if state.browser_visible and key.lower() == "l":
                entry = state.tree_entries[state.selected_idx]
                if entry.is_dir:
                    resolved = entry.path.resolve()
                    if resolved not in state.expanded:
                        state.expanded.add(resolved)
                        rebuild_tree_entries(preferred_path=resolved)
                        preview_selected_entry()
                        state.dirty = True
                    else:
                        next_idx = state.selected_idx + 1
                        if next_idx < len(state.tree_entries) and state.tree_entries[next_idx].depth > entry.depth:
                            state.selected_idx = next_idx
                            preview_selected_entry()
                            state.dirty = True
                else:
                    state.current_path = entry.path.resolve()
                    refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
                    state.dirty = True
                continue
            if state.browser_visible and key.lower() == "h":
                entry = state.tree_entries[state.selected_idx]
                if (
                    entry.is_dir
                    and entry.path.resolve() in state.expanded
                    and entry.path.resolve() != state.tree_root
                ):
                    state.expanded.remove(entry.path.resolve())
                    rebuild_tree_entries(preferred_path=entry.path.resolve())
                    preview_selected_entry()
                    state.dirty = True
                elif entry.path.resolve() != state.tree_root:
                    parent = entry.path.parent.resolve()
                    for idx, candidate in enumerate(state.tree_entries):
                        if candidate.path.resolve() == parent:
                            state.selected_idx = idx
                            preview_selected_entry()
                            state.dirty = True
                            break
                continue
            if state.browser_visible and key == "ENTER":
                entry = state.tree_entries[state.selected_idx]
                if entry.is_dir:
                    resolved = entry.path.resolve()
                    if resolved in state.expanded:
                        if resolved != state.tree_root:
                            state.expanded.remove(resolved)
                    else:
                        state.expanded.add(resolved)
                    rebuild_tree_entries(preferred_path=resolved)
                    preview_selected_entry()
                    state.dirty = True
                    continue

            prev_start = state.start
            prev_text_x = state.text_x
            scrolling_down = False
            page_rows = visible_content_rows()
            if key == " " or key.lower() == "f":
                pages = count if count is not None else 1
                state.start += page_rows * max(1, pages)
                scrolling_down = True
            elif key.lower() == "d":
                mult = count if count is not None else 1
                state.start += max(1, page_rows // 2) * max(1, mult)
                scrolling_down = True
            elif key.lower() == "u":
                mult = count if count is not None else 1
                state.start -= max(1, page_rows // 2) * max(1, mult)
            elif key == "DOWN" or (not state.browser_visible and key.lower() == "j"):
                state.start += count if count is not None else 1
                scrolling_down = True
            elif key == "UP" or (not state.browser_visible and key.lower() == "k"):
                state.start -= count if count is not None else 1
            elif key == "g":
                if count is None:
                    state.start = 0
                else:
                    state.start = max(0, min(count - 1, state.max_start))
            elif key == "G":
                if count is None:
                    state.start = state.max_start
                else:
                    state.start = max(0, min(count - 1, state.max_start))
                scrolling_down = True
            elif key == "ENTER":
                state.start += count if count is not None else 1
                scrolling_down = True
            elif key == "B":
                pages = count if count is not None else 1
                state.start -= page_rows * max(1, pages)
            elif (key == "LEFT" or (not state.browser_visible and key.lower() == "h")) and not state.wrap_text:
                step = count if count is not None else 4
                state.text_x = max(0, state.text_x - max(1, step))
            elif (key == "RIGHT" or (not state.browser_visible and key.lower() == "l")) and not state.wrap_text:
                step = count if count is not None else 4
                state.text_x += max(1, step)
            elif key == "HOME":
                state.start = 0
            elif key == "END":
                state.start = state.max_start
            elif key == "ESC":
                break

            state.start = max(0, min(state.start, state.max_start))
            grew_preview = scrolling_down and maybe_grow_directory_preview()
            if state.start != prev_start or state.text_x != prev_text_x or grew_preview:
                state.dirty = True
