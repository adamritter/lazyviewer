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
from .key_handlers import handle_picker_key as handle_picker_key_event, handle_tree_filter_key as handle_tree_filter_key_event
from .navigation import JumpLocation, is_named_mark_key
from .preview import (
    DIR_PREVIEW_GROWTH_STEP,
    DIR_PREVIEW_HARD_MAX_ENTRIES,
    DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    build_rendered_for_path,
)
from .search import search_project_content_rg
from .render import RenderContext, help_panel_row_count, render_dual_page_context
from .state import AppState
from .symbols import collect_symbols
from .terminal import TerminalController
from .watch import build_git_watch_signature, build_tree_watch_signature, resolve_git_paths
from .tree import (
    build_tree_entries,
    clamp_left_width,
    compute_left_width,
    filter_tree_entries_for_content_matches,
    filter_tree_entries_for_files,
    find_content_hit_index,
    next_directory_entry_index,
    next_index_after_directory_subtree,
    next_file_entry_index,
    next_opened_directory_entry_index,
)

DOUBLE_CLICK_SECONDS = 0.35
PICKER_RESULT_LIMIT = 200
FILTER_CURSOR_BLINK_SECONDS = 0.5
TREE_FILTER_SPINNER_FRAME_SECONDS = 0.12
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
TREE_WATCH_POLL_SECONDS = 0.5
GIT_WATCH_POLL_SECONDS = 0.5

COMMAND_PALETTE_ITEMS: tuple[tuple[str, str], ...] = (
    ("filter_files", "Filter files (Ctrl+P)"),
    ("search_content", "Search content (/)"),
    ("open_symbols", "Open symbol outline (s)"),
    ("history_back", "Jump back (Alt+Left)"),
    ("history_forward", "Jump forward (Alt+Right)"),
    ("toggle_tree", "Toggle tree pane (t)"),
    ("toggle_wrap", "Toggle wrap (w)"),
    ("toggle_hidden", "Toggle hidden files (.)"),
    ("toggle_help", "Toggle help (?)"),
    ("reroot_selected", "Set root to selected (r)"),
    ("reroot_parent", "Set root to parent (R)"),
    ("quit", "Quit (q)"),
)


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
        preview_image_path=initial_render.image_path,
        preview_image_format=initial_render.image_format,
    )

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    terminal = TerminalController(stdin_fd, stdout_fd)
    kitty_graphics_supported = terminal.supports_kitty_graphics()
    kitty_image_state: tuple[str, int, int, int, int] | None = None
    tree_filter_cursor_visible = True
    tree_filter_spinner_frame = 0
    tree_filter_loading_until = 0.0
    index_warmup_lock = threading.Lock()
    index_warmup_pending: tuple[Path, bool] | None = None
    index_warmup_running = False
    tree_watch_last_poll = 0.0
    tree_watch_signature: str | None = None
    git_watch_last_poll = 0.0
    git_watch_signature: str | None = None
    git_watch_repo_root: Path | None = None
    git_watch_dir: Path | None = None

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

    def current_preview_image_path() -> Path | None:
        if not kitty_graphics_supported:
            return None
        if state.preview_image_format != "png":
            return None
        if state.preview_image_path is None:
            return None
        try:
            image_path = state.preview_image_path.resolve()
        except Exception:
            image_path = state.preview_image_path
        if not image_path.exists() or not image_path.is_file():
            return None
        return image_path

    def current_preview_image_geometry(columns: int) -> tuple[int, int, int, int]:
        image_rows = visible_content_rows()
        if state.browser_visible:
            image_col = state.left_width + 2
            image_width = max(1, columns - state.left_width - 2 - 1)
        else:
            image_col = 1
            image_width = max(1, columns - 1)
        return image_col, 1, image_width, image_rows

    def refresh_git_status_overlay(force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - state.git_status_last_refresh) < GIT_STATUS_REFRESH_SECONDS:
            return

        previous = state.git_status_overlay
        state.git_status_overlay = collect_git_status_overlay(state.tree_root)
        state.git_status_last_refresh = time.monotonic()
        if state.git_status_overlay != previous:
            state.dirty = True

    def reset_git_watch_context() -> None:
        nonlocal git_watch_last_poll, git_watch_signature, git_watch_repo_root, git_watch_dir
        git_watch_repo_root, git_watch_dir = resolve_git_paths(state.tree_root)
        git_watch_last_poll = 0.0
        git_watch_signature = None

    def maybe_refresh_tree_watch() -> None:
        nonlocal tree_watch_last_poll, tree_watch_signature
        now = time.monotonic()
        if (now - tree_watch_last_poll) < TREE_WATCH_POLL_SECONDS:
            return
        tree_watch_last_poll = now

        signature = build_tree_watch_signature(
            state.tree_root,
            state.expanded,
            state.show_hidden,
        )
        if tree_watch_signature is None:
            tree_watch_signature = signature
            return
        if signature == tree_watch_signature:
            return

        tree_watch_signature = signature
        preferred_path = (
            state.tree_entries[state.selected_idx].path.resolve()
            if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries)
            else state.current_path.resolve()
        )
        previous_current_path = state.current_path.resolve()
        rebuild_tree_entries(preferred_path=preferred_path)
        if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries):
            selected_target = state.tree_entries[state.selected_idx].path.resolve()
        else:
            selected_target = state.tree_root.resolve()

        if selected_target == previous_current_path:
            refresh_rendered_for_current_path(reset_scroll=False, reset_dir_budget=False)
        else:
            state.current_path = selected_target
            refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
        schedule_tree_filter_index_warmup()
        refresh_git_status_overlay(force=True)
        state.dirty = True

    def maybe_refresh_git_watch() -> None:
        nonlocal git_watch_last_poll, git_watch_signature
        now = time.monotonic()
        if (now - git_watch_last_poll) < GIT_WATCH_POLL_SECONDS:
            return
        git_watch_last_poll = now

        signature = build_git_watch_signature(git_watch_dir)
        if git_watch_signature is None:
            git_watch_signature = signature
            return
        if signature == git_watch_signature:
            return

        git_watch_signature = signature
        refresh_git_status_overlay(force=True)

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

        prefer_git_diff = not (
            state.tree_filter_active
            and state.tree_filter_mode == "content"
            and bool(state.tree_filter_query)
        )
        rendered_for_path = build_rendered_for_path(
            state.current_path,
            state.show_hidden,
            style,
            no_color,
            dir_max_entries=dir_limit,
            dir_skip_gitignored=state.show_hidden,
            prefer_git_diff=prefer_git_diff,
        )
        state.rendered = rendered_for_path.text
        rebuild_screen_lines(preserve_scroll=not reset_scroll)
        state.dir_preview_truncated = rendered_for_path.truncated
        state.dir_preview_path = resolved_target if rendered_for_path.is_directory else None
        state.preview_image_path = rendered_for_path.image_path
        state.preview_image_format = rendered_for_path.image_format
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

    def next_tree_filter_result_entry_index(selected_idx: int, direction: int) -> int | None:
        if state.tree_filter_mode == "content":
            return next_content_hit_entry_index(selected_idx, direction)
        return next_file_entry_index(state.tree_entries, selected_idx, direction)

    def coerce_tree_filter_result_index(idx: int) -> int | None:
        if not (0 <= idx < len(state.tree_entries)):
            return None
        if not (state.tree_filter_active and state.tree_filter_query):
            return idx

        entry = state.tree_entries[idx]
        if state.tree_filter_mode == "content":
            if entry.kind == "search_hit":
                return idx
        elif not entry.is_dir:
            return idx

        candidate_idx = next_tree_filter_result_entry_index(idx, 1)
        if candidate_idx is None:
            candidate_idx = next_tree_filter_result_entry_index(idx, -1)
        return candidate_idx

    def move_tree_selection(direction: int) -> bool:
        if not state.tree_entries or direction == 0:
            return False

        if state.tree_filter_active and state.tree_filter_query:
            target_idx = next_tree_filter_result_entry_index(state.selected_idx, direction)
            if target_idx is None:
                return False
        else:
            step = 1 if direction > 0 else -1
            target_idx = max(0, min(len(state.tree_entries) - 1, state.selected_idx + step))

        if target_idx == state.selected_idx:
            return False

        state.selected_idx = target_idx
        preview_selected_entry()
        return True

    def parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
        parts = mouse_key.split(":")
        if len(parts) < 3:
            return None, None
        try:
            return int(parts[1]), int(parts[2])
        except Exception:
            return None, None

    def handle_tree_mouse_wheel(mouse_key: str) -> bool:
        if not (mouse_key.startswith("MOUSE_WHEEL_UP:") or mouse_key.startswith("MOUSE_WHEEL_DOWN:")):
            return False

        direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
        col, _row = parse_mouse_col_row(mouse_key)
        if state.browser_visible and col is not None and col <= state.left_width:
            if move_tree_selection(direction):
                state.dirty = True
            return True

        prev_start = state.start
        state.start += direction * 3
        state.start = max(0, min(state.start, state.max_start))
        grew_preview = direction > 0 and maybe_grow_directory_preview()
        if state.start != prev_start or grew_preview:
            state.dirty = True
        return True

    def handle_tree_mouse_click(mouse_key: str) -> bool:
        nonlocal tree_watch_signature
        if not mouse_key.startswith("MOUSE_LEFT_DOWN:"):
            return False

        col, row = parse_mouse_col_row(mouse_key)
        if not (
            state.browser_visible
            and col is not None
            and row is not None
            and 1 <= row <= visible_content_rows()
            and col <= state.left_width
        ):
            return True

        query_row_visible = state.tree_filter_active
        if query_row_visible and row == 1:
            state.tree_filter_editing = True
            state.dirty = True
            return True

        clicked_idx = state.tree_start + (row - 1 - (1 if query_row_visible else 0))
        clicked_idx = coerce_tree_filter_result_index(clicked_idx)
        if clicked_idx is None:
            return True

        prev_selected = state.selected_idx
        state.selected_idx = clicked_idx
        preview_selected_entry()
        if state.selected_idx != prev_selected:
            state.dirty = True

        now = time.monotonic()
        is_double = clicked_idx == state.last_click_idx and (now - state.last_click_time) <= DOUBLE_CLICK_SECONDS
        state.last_click_idx = clicked_idx
        state.last_click_time = now
        if not is_double:
            return True

        if state.tree_filter_active and state.tree_filter_query:
            activate_tree_filter_selection()
            return True

        entry = state.tree_entries[state.selected_idx]
        if entry.is_dir:
            resolved = entry.path.resolve()
            if resolved in state.expanded:
                state.expanded.remove(resolved)
            else:
                state.expanded.add(resolved)
            rebuild_tree_entries(preferred_path=resolved)
            tree_watch_signature = None
            state.dirty = True
            return True

        origin = current_jump_location()
        state.current_path = entry.path.resolve()
        refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
        record_jump_if_changed(origin)
        state.dirty = True
        return True

    def first_tree_filter_result_index() -> int | None:
        return next_tree_filter_result_entry_index(-1, 1)

    def jump_to_next_content_hit(direction: int) -> bool:
        if direction == 0:
            return False
        if direction > 0:
            target_idx = next_content_hit_entry_index(state.selected_idx, 1)
            if target_idx is None:
                target_idx = next_content_hit_entry_index(-1, 1)
        else:
            target_idx = next_content_hit_entry_index(state.selected_idx, -1)
            if target_idx is None:
                target_idx = next_content_hit_entry_index(len(state.tree_entries), -1)

        if target_idx is None or target_idx == state.selected_idx:
            return False

        origin = current_jump_location()
        state.selected_idx = target_idx
        preview_selected_entry()
        record_jump_if_changed(origin)
        return True

    def rebuild_tree_entries(
        preferred_path: Path | None = None,
        center_selection: bool = False,
        force_first_file: bool = False,
    ) -> None:
        previous_selected_hit_path: Path | None = None
        previous_selected_hit_line: int | None = None
        previous_selected_hit_column: int | None = None
        if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries):
            previous_entry = state.tree_entries[state.selected_idx]
            if previous_entry.kind == "search_hit":
                previous_selected_hit_path = previous_entry.path.resolve()
                previous_selected_hit_line = previous_entry.line
                previous_selected_hit_column = previous_entry.column

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
                raw_matched = fuzzy_match_label_index(
                    state.tree_filter_query,
                    state.picker_file_labels,
                    labels_folded=labels_folded,
                    limit=max(1, match_limit + 1),
                )
                state.tree_filter_truncated = len(raw_matched) > match_limit
                matched = raw_matched[:match_limit] if match_limit > 0 else []
                root = state.tree_root.resolve()
                matched_paths = [root / label for _, label, _ in matched]
                state.tree_filter_match_count = len(matched_paths)
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
            matched_preferred = False
            if (
                state.tree_filter_active
                and state.tree_filter_query
                and state.tree_filter_mode == "content"
                and previous_selected_hit_path is not None
            ):
                preserved_hit_idx = find_content_hit_index(
                    state.tree_entries,
                    previous_selected_hit_path,
                    preferred_line=previous_selected_hit_line,
                    preferred_column=previous_selected_hit_column,
                )
                if preserved_hit_idx is not None:
                    state.selected_idx = preserved_hit_idx
                    matched_preferred = True

            if not matched_preferred:
                for idx, entry in enumerate(state.tree_entries):
                    if entry.kind == "search_hit":
                        continue
                    if entry.path.resolve() == preferred_target:
                        state.selected_idx = idx
                        matched_preferred = True
                        break

            if not matched_preferred:
                if state.tree_filter_active and state.tree_filter_query:
                    first_idx = first_tree_filter_result_index()
                    state.selected_idx = first_idx if first_idx is not None else 0
                else:
                    state.selected_idx = default_selected_index(prefer_files=bool(state.tree_filter_query))

            if (
                state.tree_filter_active
                and state.tree_filter_query
                and state.tree_filter_mode == "content"
            ):
                coerced_idx = coerce_tree_filter_result_index(state.selected_idx)
                state.selected_idx = coerced_idx if coerced_idx is not None else 0

        if center_selection:
            rows = tree_view_rows()
            state.tree_start = max(0, state.selected_idx - max(1, rows // 2))

    def apply_tree_filter_query(
        query: str,
        preview_selection: bool = False,
        select_first_file: bool = False,
    ) -> None:
        nonlocal tree_filter_loading_until
        state.tree_filter_query = query
        if query:
            tree_filter_loading_until = time.monotonic() + 0.35
        else:
            tree_filter_loading_until = 0.0
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
        state.tree_filter_loading = False
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
        state.tree_filter_loading = False
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
            if state.tree_filter_mode == "content":
                state.tree_filter_editing = False
                state.dirty = True
            else:
                close_tree_filter(clear_query=True)
            return

        entry = state.tree_entries[state.selected_idx]
        if entry.is_dir:
            candidate_idx = next_tree_filter_result_entry_index(state.selected_idx, 1)
            if candidate_idx is None:
                candidate_idx = next_tree_filter_result_entry_index(state.selected_idx, -1)
            if candidate_idx is None:
                close_tree_filter(clear_query=True)
                return
            state.selected_idx = candidate_idx
            entry = state.tree_entries[state.selected_idx]

        selected_path = entry.path.resolve()
        selected_line = entry.line if entry.kind == "search_hit" else None
        if state.tree_filter_mode == "content":
            # Keep content-search mode active after Enter/double-click; Esc exits.
            origin = current_jump_location()
            state.tree_filter_editing = False
            preview_selected_entry(force=True)
            record_jump_if_changed(origin)
            state.dirty = True
            return

        origin = current_jump_location()
        close_tree_filter(clear_query=True)
        jump_to_path(selected_path)
        if selected_line is not None:
            jump_to_line(max(0, selected_line - 1))
        record_jump_if_changed(origin)
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
        state.picker_match_commands = []
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

    def refresh_command_picker_matches(reset_selection: bool = False) -> None:
        matched = fuzzy_match_labels(
            state.picker_query,
            state.picker_command_labels,
            limit=PICKER_RESULT_LIMIT,
        )
        state.picker_matches = []
        state.picker_match_labels = [label for _, label, _ in matched]
        state.picker_match_lines = []
        state.picker_match_commands = [state.picker_command_ids[idx] for idx, _, _ in matched]
        if state.picker_match_labels:
            state.picker_message = ""
        elif not state.picker_message:
            state.picker_message = " no matching commands"
        state.picker_selected = 0 if reset_selection else max(
            0,
            min(state.picker_selected, max(0, len(state.picker_match_labels) - 1)),
        )
        if reset_selection or not state.picker_match_labels:
            state.picker_list_start = 0

    def refresh_active_picker_matches(reset_selection: bool = False) -> None:
        if state.picker_mode == "commands":
            refresh_command_picker_matches(reset_selection=reset_selection)
            return
        refresh_symbol_picker_matches(reset_selection=reset_selection)

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
        state.picker_match_commands = []
        state.picker_command_ids = []
        state.picker_command_labels = []
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

    def open_command_picker() -> None:
        if not state.picker_active:
            state.picker_prev_browser_visible = state.browser_visible
        state.picker_active = True
        state.picker_mode = "commands"
        state.picker_focus = "tree"
        state.picker_message = ""
        state.picker_query = ""
        state.picker_selected = 0
        state.picker_list_start = 0
        state.picker_matches = []
        state.picker_match_labels = []
        state.picker_match_lines = []
        state.picker_match_commands = []
        state.picker_symbol_file = None
        state.picker_symbol_labels = []
        state.picker_symbol_lines = []
        state.picker_command_ids = [command_id for command_id, _ in COMMAND_PALETTE_ITEMS]
        state.picker_command_labels = [label for _, label in COMMAND_PALETTE_ITEMS]
        was_browser_visible = state.browser_visible
        state.browser_visible = True
        if state.wrap_text and not was_browser_visible:
            rebuild_screen_lines()

        refresh_command_picker_matches(reset_selection=True)
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
        state.picker_match_commands = []
        state.picker_symbol_file = None
        state.picker_symbol_labels = []
        state.picker_symbol_lines = []
        state.picker_command_ids = []
        state.picker_command_labels = []
        state.picker_prev_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = state.browser_visible != previous_browser_visible
            state.browser_visible = previous_browser_visible
            if state.wrap_text and browser_visibility_changed:
                rebuild_screen_lines()
        state.dirty = True

    def current_term_columns() -> int:
        return shutil.get_terminal_size((80, 24)).columns

    def reroot_to_parent() -> None:
        nonlocal tree_watch_signature
        old_root = state.tree_root.resolve()
        parent_root = old_root.parent.resolve()
        if parent_root == old_root:
            return
        state.tree_root = parent_root
        state.expanded = {state.tree_root, old_root}
        rebuild_tree_entries(preferred_path=old_root, center_selection=True)
        preview_selected_entry(force=True)
        schedule_tree_filter_index_warmup()
        tree_watch_signature = None
        reset_git_watch_context()
        refresh_git_status_overlay(force=True)
        state.dirty = True

    def reroot_to_selected_target() -> None:
        nonlocal tree_watch_signature
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
        if target_root == old_root:
            return
        state.tree_root = target_root
        state.expanded = {state.tree_root}
        rebuild_tree_entries(preferred_path=selected_target, center_selection=True)
        preview_selected_entry(force=True)
        schedule_tree_filter_index_warmup()
        tree_watch_signature = None
        reset_git_watch_context()
        refresh_git_status_overlay(force=True)
        state.dirty = True

    def toggle_hidden_files() -> None:
        nonlocal tree_watch_signature
        state.show_hidden = not state.show_hidden
        save_show_hidden(state.show_hidden)
        selected_path = state.tree_entries[state.selected_idx].path.resolve() if state.tree_entries else state.tree_root
        rebuild_tree_entries(preferred_path=selected_path)
        preview_selected_entry(force=True)
        schedule_tree_filter_index_warmup()
        tree_watch_signature = None
        state.dirty = True

    def toggle_tree_pane() -> None:
        state.browser_visible = not state.browser_visible
        if state.wrap_text:
            rebuild_screen_lines(columns=current_term_columns())
        state.dirty = True

    def toggle_wrap_mode() -> None:
        state.wrap_text = not state.wrap_text
        if state.wrap_text:
            state.text_x = 0
        rebuild_screen_lines(columns=current_term_columns())
        state.dirty = True

    def toggle_help_panel() -> None:
        state.show_help = not state.show_help
        rebuild_screen_lines(columns=current_term_columns())
        state.dirty = True

    def execute_command_palette_action(command_id: str) -> bool:
        if command_id == "filter_files":
            open_tree_filter(mode="files")
            return False
        if command_id == "search_content":
            open_tree_filter(mode="content")
            return False
        if command_id == "open_symbols":
            open_symbol_picker()
            return False
        if command_id == "history_back":
            if jump_back_in_history():
                state.dirty = True
            return False
        if command_id == "history_forward":
            if jump_forward_in_history():
                state.dirty = True
            return False
        if command_id == "toggle_tree":
            toggle_tree_pane()
            return False
        if command_id == "toggle_wrap":
            toggle_wrap_mode()
            return False
        if command_id == "toggle_hidden":
            toggle_hidden_files()
            return False
        if command_id == "toggle_help":
            toggle_help_panel()
            return False
        if command_id == "reroot_selected":
            reroot_to_selected_target()
            return False
        if command_id == "reroot_parent":
            reroot_to_parent()
            return False
        if command_id == "quit":
            return True
        return False

    def current_jump_location() -> JumpLocation:
        return JumpLocation(
            path=state.current_path.resolve(),
            start=max(0, state.start),
            text_x=max(0, state.text_x),
        )

    def record_jump_if_changed(origin: JumpLocation) -> None:
        normalized_origin = origin.normalized()
        if current_jump_location() == normalized_origin:
            return
        state.jump_history.record(normalized_origin)

    def apply_jump_location(location: JumpLocation) -> bool:
        target = location.normalized()
        current_path = state.current_path.resolve()
        path_changed = target.path != current_path
        if path_changed:
            jump_to_path(target.path)

        state.max_start = max(0, len(state.lines) - visible_content_rows())
        clamped_start = max(0, min(target.start, state.max_start))
        clamped_text_x = 0 if state.wrap_text else max(0, target.text_x)
        prev_start = state.start
        prev_text_x = state.text_x
        state.start = clamped_start
        state.text_x = clamped_text_x
        return path_changed or state.start != prev_start or state.text_x != prev_text_x

    def jump_back_in_history() -> bool:
        target = state.jump_history.go_back(current_jump_location())
        if target is None:
            return False
        return apply_jump_location(target)

    def jump_forward_in_history() -> bool:
        target = state.jump_history.go_forward(current_jump_location())
        if target is None:
            return False
        return apply_jump_location(target)

    def set_named_mark(mark_key: str) -> bool:
        if not is_named_mark_key(mark_key):
            return False
        state.named_marks[mark_key] = current_jump_location()
        return True

    def jump_to_named_mark(mark_key: str) -> bool:
        if not is_named_mark_key(mark_key):
            return False
        target = state.named_marks.get(mark_key)
        if target is None:
            return False
        origin = current_jump_location()
        if target.normalized() == origin:
            return False
        state.jump_history.record(origin)
        return apply_jump_location(target)

    def activate_picker_selection() -> bool:
        if state.picker_mode == "symbols" and state.picker_match_lines:
            selected_line = state.picker_match_lines[state.picker_selected]
            symbol_file = state.picker_symbol_file
            origin = current_jump_location()
            close_picker()
            if symbol_file is not None and symbol_file.resolve() != state.current_path.resolve():
                jump_to_path(symbol_file.resolve())
            jump_to_line(selected_line)
            record_jump_if_changed(origin)
            return False
        if state.picker_mode == "commands" and state.picker_match_commands:
            command_id = state.picker_match_commands[state.picker_selected]
            close_picker()
            return execute_command_palette_action(command_id)
        return False

    def reveal_path_in_tree(target: Path) -> None:
        nonlocal tree_watch_signature
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
        tree_watch_signature = None

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
    tree_watch_signature = build_tree_watch_signature(
        state.tree_root,
        state.expanded,
        state.show_hidden,
    )
    tree_watch_last_poll = time.monotonic()
    reset_git_watch_context()
    git_watch_signature = build_git_watch_signature(git_watch_dir)
    git_watch_last_poll = time.monotonic()
    refresh_git_status_overlay(force=True)



    def handle_normal_key(key: str, term_columns: int) -> bool:
        nonlocal tree_watch_signature

        if key.lower() == "s" and not state.picker_active:
            state.count_buffer = ""
            open_symbol_picker()
            return False

        if key == "m":
            state.count_buffer = ""
            state.pending_mark_set = True
            state.pending_mark_jump = False
            return False

        if key == "'":
            state.count_buffer = ""
            state.pending_mark_set = False
            state.pending_mark_jump = True
            return False

        if key.isdigit():
            state.count_buffer += key
            return False

        count = int(state.count_buffer) if state.count_buffer else None
        state.count_buffer = ""
        if key == "?":
            toggle_help_panel()
            return False
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
                    origin = current_jump_location()
                    prev_selected = state.selected_idx
                    state.selected_idx = target_idx
                    preview_selected_entry()
                    record_jump_if_changed(origin)
                    if state.selected_idx != prev_selected or current_jump_location() != origin:
                        state.dirty = True
            return False
        if key == "R":
            reroot_to_parent()
            return False
        if key == "r":
            reroot_to_selected_target()
            return False
        if key == ".":
            toggle_hidden_files()
            return False
        if key.lower() == "t":
            toggle_tree_pane()
            return False
        if key.lower() == "w":
            toggle_wrap_mode()
            return False
        if key.lower() == "e":
            edit_target: Path | None = None
            if state.browser_visible and state.tree_entries:
                selected_entry = state.tree_entries[state.selected_idx]
                if not selected_entry.is_dir and selected_entry.path.is_file():
                    edit_target = selected_entry.path.resolve()
            if edit_target is None and state.current_path.is_file():
                edit_target = state.current_path.resolve()
            if edit_target is None:
                state.rendered = "[31m<cannot edit a directory>[0m"
                rebuild_screen_lines(columns=term_columns, preserve_scroll=False)
                state.text_x = 0
                state.dirty = True
                return False

            error = launch_editor(edit_target, terminal.disable_tui_mode, terminal.enable_tui_mode)
            state.current_path = edit_target
            if error is None:
                refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
                refresh_git_status_overlay(force=True)
            else:
                state.rendered = f"[31m{error}[0m"
                rebuild_screen_lines(columns=term_columns, preserve_scroll=False)
                state.text_x = 0
                state.dir_preview_path = None
                state.dir_preview_truncated = False
                state.preview_image_path = None
                state.preview_image_format = None
            state.dirty = True
            return False
        if key.lower() == "q" or key == "\x03":
            return True
        if handle_tree_mouse_wheel(key):
            return False
        if handle_tree_mouse_click(key):
            return False
        if state.browser_visible and key.lower() == "j":
            if move_tree_selection(1):
                state.dirty = True
            return False
        if state.browser_visible and key.lower() == "k":
            if move_tree_selection(-1):
                state.dirty = True
            return False
        if state.browser_visible and key.lower() == "l":
            entry = state.tree_entries[state.selected_idx]
            if entry.is_dir:
                resolved = entry.path.resolve()
                if resolved not in state.expanded:
                    state.expanded.add(resolved)
                    rebuild_tree_entries(preferred_path=resolved)
                    tree_watch_signature = None
                    preview_selected_entry()
                    state.dirty = True
                else:
                    next_idx = state.selected_idx + 1
                    if next_idx < len(state.tree_entries) and state.tree_entries[next_idx].depth > entry.depth:
                        state.selected_idx = next_idx
                        preview_selected_entry()
                        state.dirty = True
            else:
                origin = current_jump_location()
                state.current_path = entry.path.resolve()
                refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
                record_jump_if_changed(origin)
                state.dirty = True
            return False
        if state.browser_visible and key.lower() == "h":
            entry = state.tree_entries[state.selected_idx]
            if (
                entry.is_dir
                and entry.path.resolve() in state.expanded
                and entry.path.resolve() != state.tree_root
            ):
                state.expanded.remove(entry.path.resolve())
                rebuild_tree_entries(preferred_path=entry.path.resolve())
                tree_watch_signature = None
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
            return False
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
                tree_watch_signature = None
                preview_selected_entry()
                state.dirty = True
                return False

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
            return True

        state.start = max(0, min(state.start, state.max_start))
        grew_preview = scrolling_down and maybe_grow_directory_preview()
        if state.start != prev_start or state.text_x != prev_text_x or grew_preview:
            state.dirty = True
        return False
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

            if state.picker_active and state.picker_mode in {"symbols", "commands"}:
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

            loading_active = bool(
                state.tree_filter_active
                and state.tree_filter_query
                and not state.picker_active
                and time.monotonic() < tree_filter_loading_until
            )
            if loading_active != state.tree_filter_loading:
                state.tree_filter_loading = loading_active
                state.dirty = True
            if state.tree_filter_loading:
                next_spinner_frame = int(time.monotonic() / TREE_FILTER_SPINNER_FRAME_SECONDS)
                if next_spinner_frame != tree_filter_spinner_frame:
                    tree_filter_spinner_frame = next_spinner_frame
                    state.dirty = True

            maybe_refresh_tree_watch()
            maybe_refresh_git_watch()
            refresh_git_status_overlay()

            if state.dirty:
                preview_image_path = current_preview_image_path()
                render_lines = [""] if preview_image_path is not None else state.lines
                render_start = 0 if preview_image_path is not None else state.start
                render_context = RenderContext(
                    text_lines=render_lines,
                    text_start=render_start,
                    tree_entries=state.tree_entries,
                    tree_start=state.tree_start,
                    tree_selected=state.selected_idx,
                    max_lines=state.usable,
                    current_path=state.current_path,
                    tree_root=state.tree_root,
                    expanded=state.tree_render_expanded,
                    width=term.columns,
                    left_width=state.left_width,
                    text_x=state.text_x,
                    wrap_text=state.wrap_text,
                    browser_visible=state.browser_visible,
                    show_hidden=state.show_hidden,
                    show_help=state.show_help,
                    tree_filter_active=state.tree_filter_active,
                    tree_filter_query=state.tree_filter_query,
                    tree_filter_editing=state.tree_filter_editing,
                    tree_filter_cursor_visible=tree_filter_cursor_visible,
                    tree_filter_match_count=state.tree_filter_match_count,
                    tree_filter_truncated=state.tree_filter_truncated,
                    tree_filter_loading=state.tree_filter_loading,
                    tree_filter_spinner_frame=tree_filter_spinner_frame,
                    tree_filter_prefix=tree_filter_prompt_prefix(),
                    tree_filter_placeholder=tree_filter_placeholder(),
                    picker_active=state.picker_active,
                    picker_mode=state.picker_mode,
                    picker_query=state.picker_query,
                    picker_items=state.picker_match_labels,
                    picker_selected=state.picker_selected,
                    picker_focus=state.picker_focus,
                    picker_list_start=state.picker_list_start,
                    picker_message=state.picker_message,
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
                    text_search_current_line=(
                        state.tree_entries[state.selected_idx].line or 0
                        if (
                            state.tree_filter_active
                            and state.tree_filter_mode == "content"
                            and state.tree_filter_query
                            and 0 <= state.selected_idx < len(state.tree_entries)
                            and state.tree_entries[state.selected_idx].kind == "search_hit"
                            and state.tree_entries[state.selected_idx].line is not None
                        )
                        else 0
                    ),
                    text_search_current_column=(
                        state.tree_entries[state.selected_idx].column or 0
                        if (
                            state.tree_filter_active
                            and state.tree_filter_mode == "content"
                            and state.tree_filter_query
                            and 0 <= state.selected_idx < len(state.tree_entries)
                            and state.tree_entries[state.selected_idx].kind == "search_hit"
                            and state.tree_entries[state.selected_idx].column is not None
                        )
                        else 0
                    ),
                )
                render_dual_page_context(render_context)
                desired_image_state: tuple[str, int, int, int, int] | None = None
                if preview_image_path is not None:
                    image_col, image_row, image_width, image_height = current_preview_image_geometry(term.columns)
                    desired_image_state = (
                        str(preview_image_path),
                        image_col,
                        image_row,
                        image_width,
                        image_height,
                    )
                if desired_image_state != kitty_image_state:
                    if kitty_image_state is not None:
                        terminal.kitty_clear_images()
                    if desired_image_state is not None and preview_image_path is not None:
                        terminal.kitty_draw_png(
                            preview_image_path,
                            col=desired_image_state[1],
                            row=desired_image_state[2],
                            width_cells=desired_image_state[3],
                            height_cells=desired_image_state[4],
                        )
                    kitty_image_state = desired_image_state
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
                if state.tree_filter_active and state.tree_filter_editing and not state.picker_active:
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

            if state.pending_mark_set:
                state.pending_mark_set = False
                state.pending_mark_jump = False
                state.count_buffer = ""
                if key == "ESC":
                    continue
                if set_named_mark(key):
                    state.dirty = True
                continue

            if state.pending_mark_jump:
                state.pending_mark_set = False
                state.pending_mark_jump = False
                state.count_buffer = ""
                if key == "ESC":
                    continue
                if jump_to_named_mark(key):
                    state.dirty = True
                continue

            if key == "ALT_LEFT" and not state.picker_active and not (
                state.tree_filter_active and state.tree_filter_editing
            ):
                state.count_buffer = ""
                if jump_back_in_history():
                    state.dirty = True
                continue

            if key == "ALT_RIGHT" and not state.picker_active and not (
                state.tree_filter_active and state.tree_filter_editing
            ):
                state.count_buffer = ""
                if jump_forward_in_history():
                    state.dirty = True
                continue

            if key == "CTRL_P" and not state.picker_active:
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

            if key == ":" and not state.picker_active:
                state.count_buffer = ""
                open_command_picker()
                continue

            picker_handled, picker_should_quit = handle_picker_key_event(
                key=key,
                state=state,
                double_click_seconds=DOUBLE_CLICK_SECONDS,
                close_picker=close_picker,
                refresh_command_picker_matches=refresh_command_picker_matches,
                activate_picker_selection=activate_picker_selection,
                visible_content_rows=visible_content_rows,
                refresh_active_picker_matches=refresh_active_picker_matches,
            )
            if picker_should_quit:
                break
            if picker_handled:
                continue
            if handle_tree_filter_key_event(
                key=key,
                state=state,
                handle_tree_mouse_wheel=handle_tree_mouse_wheel,
                handle_tree_mouse_click=handle_tree_mouse_click,
                close_tree_filter=close_tree_filter,
                activate_tree_filter_selection=activate_tree_filter_selection,
                move_tree_selection=move_tree_selection,
                apply_tree_filter_query=apply_tree_filter_query,
                jump_to_next_content_hit=jump_to_next_content_hit,
            ):
                continue
            if handle_normal_key(key, term.columns):
                break
