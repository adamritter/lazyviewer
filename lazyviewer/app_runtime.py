"""Runtime composition layer for lazyviewer.

Builds initial state, wires callbacks across runtime modules, and starts the loop.
This is the highest-level module where rendering, navigation, search, and git meet.
"""

from __future__ import annotations

import os
import re
import subprocess
import shutil
import sys
import threading
import time
from pathlib import Path

from .ansi import ANSI_ESCAPE_RE, build_screen_lines, char_display_width
from .config import (
    load_content_search_left_pane_percent,
    load_left_pane_percent,
    load_named_marks,
    save_content_search_left_pane_percent,
    save_left_pane_percent,
    load_show_hidden,
)
from .editor import launch_editor
from .git_status import clear_diff_preview_cache, collect_git_status_overlay
from .highlight import colorize_source
from .key_handlers import NormalKeyOps, handle_normal_key as handle_normal_key_event
from .preview import (
    DIR_PREVIEW_GROWTH_STEP,
    DIR_PREVIEW_HARD_MAX_ENTRIES,
    DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    build_rendered_for_path,
    clear_directory_preview_cache,
)
from .render import help_panel_row_count
from .runtime_loop import RuntimeLoopCallbacks, RuntimeLoopTiming, run_main_loop
from .runtime_navigation import NavigationPickerOps
from .runtime_tree_filter import TreeFilterOps
from .search.fuzzy import collect_project_file_labels
from .state import AppState
from .terminal import TerminalController
from .tree import (
    build_tree_entries,
    clamp_left_width,
    compute_left_width,
)
from .watch import build_git_watch_signature, build_tree_watch_signature, resolve_git_paths

DOUBLE_CLICK_SECONDS = 0.35
FILTER_CURSOR_BLINK_SECONDS = 0.5
TREE_FILTER_SPINNER_FRAME_SECONDS = 0.12
GIT_STATUS_REFRESH_SECONDS = 2.0
TREE_WATCH_POLL_SECONDS = 0.5
GIT_WATCH_POLL_SECONDS = 0.5
GIT_FEATURES_DEFAULT_ENABLED = True
CONTENT_SEARCH_LEFT_PANE_MIN_PERCENT = 50.0
CONTENT_SEARCH_LEFT_PANE_FALLBACK_DELTA_PERCENT = 8.0
SOURCE_SELECTION_DRAG_SCROLL_SPEED_NUMERATOR = 2
SOURCE_SELECTION_DRAG_SCROLL_SPEED_DENOMINATOR = 1
_TRAILING_GIT_BADGES_RE = re.compile(r"^(.*?)(?:\s(?:\[(?:M|\?)\])+)$")
_CLICK_SEARCH_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _skip_gitignored_for_hidden_mode(show_hidden: bool) -> bool:
    # Hidden mode should reveal both dotfiles and gitignored paths.
    return not show_hidden

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


def _line_has_git_change_marker(line: str) -> bool:
    plain = ANSI_ESCAPE_RE.sub("", line)
    if plain.startswith("+ ") or plain.startswith("- "):
        return True
    for match in ANSI_ESCAPE_RE.finditer(line):
        seq = match.group(0)
        if not seq.endswith("m"):
            continue
        if seq.startswith("\x1b[48;") or ";48;" in seq:
            return True
    return False


def _git_change_block_start_lines(screen_lines: list[str]) -> list[int]:
    starts: list[int] = []
    in_block = False
    for idx, line in enumerate(screen_lines):
        is_change = _line_has_git_change_marker(line)
        if is_change and not in_block:
            starts.append(idx)
        in_block = is_change
    return starts


def _first_git_change_screen_line(screen_lines: list[str]) -> int | None:
    starts = _git_change_block_start_lines(screen_lines)
    if not starts:
        return None
    return starts[0]


def _centered_scroll_start(target_line: int, max_start: int, visible_rows: int) -> int:
    anchor = max(0, min(target_line, max_start))
    centered = max(0, anchor - max(1, visible_rows // 3))
    return max(0, min(centered, max_start))


def _tree_order_key_for_relative_path(
    relative_path: Path,
    *,
    is_dir: bool = False,
) -> tuple[tuple[int, str, str], ...]:
    parts = relative_path.parts
    if not parts:
        return tuple()

    out: list[tuple[int, str, str]] = []
    last_index = len(parts) - 1
    for idx, part in enumerate(parts):
        if idx < last_index:
            node_kind = 0
        else:
            node_kind = 0 if is_dir else 1
        out.append((node_kind, part.casefold(), part))
    return tuple(out)


def _copy_text_to_clipboard(text: str) -> bool:
    if not text:
        return False

    command_candidates: list[list[str]] = []
    if sys.platform == "darwin":
        command_candidates.append(["pbcopy"])
    elif os.name == "nt":
        command_candidates.append(["clip"])
    else:
        command_candidates.extend(
            [
                ["wl-copy"],
                ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
            ]
        )

    for command in command_candidates:
        if shutil.which(command[0]) is None:
            continue
        try:
            proc = subprocess.run(
                command,
                input=text,
                text=True,
                check=False,
            )
        except Exception:
            continue
        if proc.returncode == 0:
            return True
    return False


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
    named_marks = load_named_marks()

    tree_entries = build_tree_entries(
        tree_root,
        expanded,
        show_hidden,
        skip_gitignored=_skip_gitignored_for_hidden_mode(show_hidden),
    )
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
        dir_skip_gitignored=_skip_gitignored_for_hidden_mode(show_hidden),
        prefer_git_diff=GIT_FEATURES_DEFAULT_ENABLED,
    )
    rendered = initial_render.text
    lines = build_screen_lines(rendered, right_width, wrap=False)
    max_start = max(0, len(lines) - usable)
    initial_start = 0
    if initial_render.is_git_diff_preview:
        first_change = _first_git_change_screen_line(lines)
        if first_change is not None:
            initial_start = _centered_scroll_start(first_change, max_start, usable)

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
        start=initial_start,
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
        preview_is_git_diff=initial_render.is_git_diff_preview,
        git_features_enabled=GIT_FEATURES_DEFAULT_ENABLED,
        named_marks=named_marks,
    )

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    terminal = TerminalController(stdin_fd, stdout_fd)
    kitty_graphics_supported = terminal.supports_kitty_graphics()
    index_warmup_lock = threading.Lock()
    index_warmup_pending: tuple[Path, bool] | None = None
    index_warmup_running = False
    tree_watch_last_poll = 0.0
    tree_watch_signature: str | None = None
    git_watch_last_poll = 0.0
    git_watch_signature: str | None = None
    git_watch_repo_root: Path | None = None
    git_watch_dir: Path | None = None
    source_selection_drag_active = False
    source_selection_drag_pointer: tuple[int, int] | None = None
    source_selection_drag_edge: str | None = None
    source_selection_drag_h_edge: str | None = None

    def reset_source_selection_drag_state() -> None:
        nonlocal source_selection_drag_active, source_selection_drag_pointer
        nonlocal source_selection_drag_edge, source_selection_drag_h_edge
        source_selection_drag_active = False
        source_selection_drag_pointer = None
        source_selection_drag_edge = None
        source_selection_drag_h_edge = None

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
                    skip_gitignored=_skip_gitignored_for_hidden_mode(show_hidden_value),
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
        help_rows = help_panel_row_count(
            state.usable,
            state.show_help,
            browser_visible=state.browser_visible,
            tree_filter_active=state.tree_filter_active,
            tree_filter_mode=state.tree_filter_mode,
            tree_filter_editing=state.tree_filter_editing,
        )
        return max(1, state.usable - help_rows)

    def content_search_match_view_active() -> bool:
        return (
            state.tree_filter_active
            and state.tree_filter_mode == "content"
            and bool(state.tree_filter_query)
        )

    content_mode_left_width_active = content_search_match_view_active()

    def sync_left_width_for_tree_filter_mode(force: bool = False) -> None:
        nonlocal content_mode_left_width_active

        use_content_mode_width = content_search_match_view_active()
        if not force and use_content_mode_width == content_mode_left_width_active:
            return
        content_mode_left_width_active = use_content_mode_width

        columns = shutil.get_terminal_size((80, 24)).columns
        if use_content_mode_width:
            saved_percent = load_content_search_left_pane_percent()
            if saved_percent is None:
                current_percent = (state.left_width / max(1, columns)) * 100.0
                saved_percent = min(
                    99.0,
                    max(
                        CONTENT_SEARCH_LEFT_PANE_MIN_PERCENT,
                        current_percent + CONTENT_SEARCH_LEFT_PANE_FALLBACK_DELTA_PERCENT,
                    ),
                )
        else:
            saved_percent = load_left_pane_percent()

        if saved_percent is None:
            desired_left = compute_left_width(columns)
        else:
            desired_left = int((saved_percent / 100.0) * columns)
        desired_left = clamp_left_width(columns, desired_left)
        if desired_left == state.left_width:
            return

        state.left_width = desired_left
        state.right_width = max(1, columns - state.left_width - 2)
        if state.right_width != state.last_right_width:
            state.last_right_width = state.right_width
            rebuild_screen_lines(columns=columns)
        state.dirty = True

    def save_left_pane_width_for_mode(total_width: int, left_width: int) -> None:
        if content_search_match_view_active():
            save_content_search_left_pane_percent(total_width, left_width)
            return
        save_left_pane_percent(total_width, left_width)

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

    def show_inline_error(message: str) -> None:
        state.rendered = f"\033[31m{message}\033[0m"
        rebuild_screen_lines(preserve_scroll=False)
        state.text_x = 0
        state.dir_preview_path = None
        state.dir_preview_truncated = False
        state.preview_image_path = None
        state.preview_image_format = None
        state.dirty = True

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
        if not state.git_features_enabled:
            if state.git_status_overlay:
                state.git_status_overlay = {}
                state.dirty = True
            state.git_status_last_refresh = time.monotonic()
            return

        now = time.monotonic()
        if not force and (now - state.git_status_last_refresh) < GIT_STATUS_REFRESH_SECONDS:
            return

        previous = state.git_status_overlay
        state.git_status_overlay = collect_git_status_overlay(state.tree_root)
        state.git_status_last_refresh = time.monotonic()
        if state.git_status_overlay != previous:
            if state.current_path.resolve().is_dir():
                refresh_rendered_for_current_path(reset_scroll=False, reset_dir_budget=False)
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
        sync_selected_target_after_tree_refresh(preferred_path=preferred_path)

    def maybe_refresh_git_watch() -> None:
        nonlocal git_watch_last_poll, git_watch_signature
        if not state.git_features_enabled:
            return
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
        # Git HEAD/index changes can invalidate the current file's diff preview
        # even when the selected path hasn't changed.
        previous_rendered = state.rendered
        previous_start = state.start
        previous_max_start = state.max_start
        refresh_rendered_for_current_path(reset_scroll=False, reset_dir_budget=False)
        if (
            state.rendered != previous_rendered
            or state.start != previous_start
            or state.max_start != previous_max_start
        ):
            state.dirty = True

    def sorted_git_modified_file_paths() -> list[Path]:
        if not state.git_features_enabled:
            return []
        if not state.git_status_overlay:
            return []

        root = state.tree_root.resolve()
        rel_to_path: dict[Path, Path] = {}
        for raw_path, flags in state.git_status_overlay.items():
            if flags == 0:
                continue
            path = raw_path.resolve()
            if path == root or not path.is_relative_to(root):
                continue
            if not path.exists() or path.is_dir():
                continue
            try:
                rel = path.relative_to(root)
            except Exception:
                continue
            if not state.show_hidden and any(part.startswith(".") for part in rel.parts):
                continue
            rel_to_path[rel] = path

        if not rel_to_path:
            return []
        ordered_rel = sorted(rel_to_path, key=_tree_order_key_for_relative_path)
        return [rel_to_path[rel] for rel in ordered_rel]

    def refresh_rendered_for_current_path(
        reset_scroll: bool = True,
        reset_dir_budget: bool = False,
        force_rebuild: bool = False,
    ) -> None:
        if force_rebuild:
            clear_directory_preview_cache()
            clear_diff_preview_cache()
        resolved_target = state.current_path.resolve()
        is_dir_target = resolved_target.is_dir()
        if is_dir_target:
            if reset_dir_budget or state.dir_preview_path != resolved_target:
                state.dir_preview_max_entries = DIR_PREVIEW_INITIAL_MAX_ENTRIES
            dir_limit = state.dir_preview_max_entries
        else:
            dir_limit = DIR_PREVIEW_INITIAL_MAX_ENTRIES

        prefer_git_diff = state.git_features_enabled and not (
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
            dir_skip_gitignored=_skip_gitignored_for_hidden_mode(state.show_hidden),
            prefer_git_diff=prefer_git_diff,
            dir_git_status_overlay=(state.git_status_overlay if state.git_features_enabled else None),
        )
        state.rendered = rendered_for_path.text
        rebuild_screen_lines(preserve_scroll=not reset_scroll)
        if reset_scroll and rendered_for_path.is_git_diff_preview:
            first_change = _first_git_change_screen_line(state.lines)
            if first_change is not None:
                state.start = _centered_scroll_start(
                    first_change,
                    state.max_start,
                    visible_content_rows(),
                )
        state.dir_preview_truncated = rendered_for_path.truncated
        state.dir_preview_path = resolved_target if rendered_for_path.is_directory else None
        state.preview_image_path = rendered_for_path.image_path
        state.preview_image_format = rendered_for_path.image_format
        state.preview_is_git_diff = rendered_for_path.is_git_diff_preview
        if reset_scroll:
            state.text_x = 0

    def clear_source_selection() -> bool:
        if state.source_selection_anchor is None and state.source_selection_focus is None:
            return False
        state.source_selection_anchor = None
        state.source_selection_focus = None
        return True

    def toggle_git_features() -> None:
        state.git_features_enabled = not state.git_features_enabled
        if state.git_features_enabled:
            refresh_git_status_overlay(force=True)
        else:
            if state.git_status_overlay:
                state.git_status_overlay = {}
            state.git_status_last_refresh = time.monotonic()
        refresh_rendered_for_current_path(
            reset_scroll=state.git_features_enabled,
            reset_dir_budget=False,
        )
        state.dirty = True

    def preview_selected_entry(force: bool = False) -> None:
        if not state.tree_entries:
            return
        entry = state.tree_entries[state.selected_idx]
        selected_target = entry.path.resolve()
        if clear_source_selection():
            state.dirty = True
        if entry.kind == "search_hit":
            if force or selected_target != state.current_path.resolve():
                state.current_path = selected_target
                refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
            if entry.line is not None:
                jump_to_line_proxy(max(0, entry.line - 1))
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

    def parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
        parts = mouse_key.split(":")
        if len(parts) < 3:
            return None, None
        try:
            return int(parts[1]), int(parts[2])
        except Exception:
            return None, None

    def preview_pane_width() -> int:
        if state.browser_visible:
            return max(1, state.right_width)
        term = shutil.get_terminal_size((80, 24))
        return max(1, term.columns - 1)

    def rendered_line_display_width(line: str) -> int:
        plain = ANSI_ESCAPE_RE.sub("", line).rstrip("\r\n")
        col = 0
        for ch in plain:
            col += char_display_width(ch, col)
        return col

    def max_horizontal_text_offset() -> int:
        if state.wrap_text or not state.lines:
            return 0
        viewport_width = preview_pane_width()
        max_width = 0
        for line in state.lines:
            max_width = max(max_width, rendered_line_display_width(line))
        return max(0, max_width - viewport_width)

    def source_pane_col_bounds() -> tuple[int, int]:
        if state.browser_visible:
            min_col = state.left_width + 2
            pane_width = max(1, state.right_width)
        else:
            min_col = 1
            pane_width = preview_pane_width()
        max_col = min_col + pane_width - 1
        return min_col, max_col

    def _drag_scroll_step(overshoot: int, span: int) -> int:
        if overshoot < 1:
            overshoot = 1
        base_step = max(1, min(max(1, span // 2), overshoot))
        return max(
            1,
            (
                base_step * SOURCE_SELECTION_DRAG_SCROLL_SPEED_NUMERATOR
                + SOURCE_SELECTION_DRAG_SCROLL_SPEED_DENOMINATOR
                - 1
            )
            // SOURCE_SELECTION_DRAG_SCROLL_SPEED_DENOMINATOR,
        )

    def source_selection_position(col: int, row: int) -> tuple[int, int] | None:
        visible_rows = visible_content_rows()
        if row < 1 or row > visible_rows:
            return None

        if state.browser_visible:
            right_start_col = state.left_width + 2
            if col < right_start_col:
                return None
            text_col = max(0, col - right_start_col + state.text_x)
        else:
            right_start_col = 1
            if col < right_start_col:
                return None
            text_col = max(0, col - right_start_col + state.text_x)

        if not state.lines:
            return None
        line_idx = max(0, min(state.start + row - 1, len(state.lines) - 1))
        return line_idx, text_col

    def _display_col_to_text_index(text: str, display_col: int) -> int:
        if display_col <= 0:
            return 0
        col = 0
        for idx, ch in enumerate(text):
            width = char_display_width(ch, col)
            next_col = col + width
            if display_col < next_col:
                return idx
            col = next_col
        return len(text)

    def clicked_preview_search_token(selection_pos: tuple[int, int]) -> str | None:
        if not state.lines:
            return None

        line_idx, text_col = selection_pos
        if line_idx < 0 or line_idx >= len(state.lines):
            return None

        plain_line = ANSI_ESCAPE_RE.sub("", state.lines[line_idx]).rstrip("\r\n")
        if not plain_line:
            return None

        clicked_index = _display_col_to_text_index(plain_line, text_col)
        candidate_indices = [clicked_index]
        if clicked_index > 0:
            candidate_indices.append(clicked_index - 1)

        for candidate in candidate_indices:
            if candidate < 0 or candidate >= len(plain_line):
                continue
            for match in _CLICK_SEARCH_TOKEN_RE.finditer(plain_line):
                if match.start() <= candidate < match.end():
                    token = match.group(0)
                    return token if token else None
        return None

    def open_content_search_for_token(query: str) -> bool:
        token = query.strip()
        if not token:
            return False
        open_tree_filter("content")
        apply_tree_filter_query(
            token,
            preview_selection=True,
            select_first_file=True,
        )
        state.tree_filter_editing = False
        state.dirty = True
        return True

    def _line_has_newline_terminator(line: str) -> bool:
        return line.endswith("\n") or line.endswith("\r")

    def display_line_to_source_line(display_idx: int) -> int | None:
        if display_idx < 0 or display_idx >= len(state.lines):
            return None
        if not state.wrap_text:
            return display_idx

        source_idx = 0
        for idx in range(display_idx):
            if _line_has_newline_terminator(state.lines[idx]):
                source_idx += 1
        return source_idx

    def directory_preview_target_for_display_line(display_idx: int) -> Path | None:
        if state.dir_preview_path is None:
            return None

        source_idx = display_line_to_source_line(display_idx)
        if source_idx is None:
            return None

        rendered_lines = state.rendered.splitlines()
        if source_idx < 0 or source_idx >= len(rendered_lines):
            return None

        root = state.dir_preview_path.resolve()
        dirs_by_depth: dict[int, Path] = {0: root}

        for idx, raw_line in enumerate(rendered_lines):
            plain_line = ANSI_ESCAPE_RE.sub("", raw_line).rstrip("\r\n")
            target: Path | None = None
            depth = 0
            is_dir = False

            if idx == 0:
                target = root
                depth = 0
                is_dir = True
            else:
                branch_idx = plain_line.find("├─ ")
                if branch_idx < 0:
                    branch_idx = plain_line.find("└─ ")
                if branch_idx >= 0:
                    name_part = plain_line[branch_idx + 3 :]
                    if name_part and not name_part.startswith("<error:"):
                        badge_match = _TRAILING_GIT_BADGES_RE.match(name_part.rstrip())
                        if badge_match is not None:
                            name_part = badge_match.group(1)
                        is_dir = name_part.endswith("/")
                        if is_dir:
                            name_part = name_part[:-1]
                        if name_part:
                            depth = (branch_idx // 3) + 1
                            parent = dirs_by_depth.get(depth - 1, root)
                            target = (parent / name_part).resolve()

            if target is not None:
                for key in [level for level in dirs_by_depth if level > depth]:
                    dirs_by_depth.pop(key, None)
                if is_dir:
                    dirs_by_depth[depth] = target
                if idx == source_idx:
                    return target

        return None

    def tick_source_selection_drag() -> None:
        if not source_selection_drag_active or state.source_selection_anchor is None:
            return
        if source_selection_drag_pointer is None:
            return

        col, row = source_selection_drag_pointer
        visible_rows = visible_content_rows()
        if visible_rows <= 0:
            return

        min_source_col, max_source_col = source_pane_col_bounds()
        target_col = max(min_source_col, min(col, max_source_col))
        changed = False

        top_edge_active = row < 1 or (row == 1 and source_selection_drag_edge == "top")
        bottom_edge_active = row > visible_rows or (
            row == visible_rows and source_selection_drag_edge == "bottom"
        )
        left_edge_active = col < min_source_col or (col == min_source_col and source_selection_drag_h_edge == "left")
        right_edge_active = col > max_source_col or (
            col == max_source_col and source_selection_drag_h_edge == "right"
        )

        if top_edge_active:
            overshoot = 1 - row
            step = _drag_scroll_step(overshoot, visible_rows)
            previous_start = state.start
            state.start = max(0, state.start - step)
            changed = state.start != previous_start
            target_row = 1
        elif bottom_edge_active:
            overshoot = row - visible_rows
            step = _drag_scroll_step(overshoot, visible_rows)
            previous_start = state.start
            state.start = min(state.max_start, state.start + step)
            grew_preview = False
            if state.start == previous_start:
                grew_preview = maybe_grow_directory_preview()
                if grew_preview:
                    state.start = min(state.max_start, state.start + step)
            changed = state.start != previous_start or grew_preview
            target_row = visible_rows
        else:
            target_row = row

        if left_edge_active:
            overshoot = min_source_col - col
            step = _drag_scroll_step(overshoot, max_source_col - min_source_col + 1)
            previous_text_x = state.text_x
            state.text_x = max(0, state.text_x - step)
            if state.text_x != previous_text_x:
                changed = True
        elif right_edge_active:
            overshoot = col - max_source_col
            step = _drag_scroll_step(overshoot, max_source_col - min_source_col + 1)
            previous_text_x = state.text_x
            state.text_x = min(max_horizontal_text_offset(), state.text_x + step)
            if state.text_x != previous_text_x:
                changed = True

        target_pos = source_selection_position(target_col, target_row)
        if target_pos is not None and target_pos != state.source_selection_focus:
            state.source_selection_focus = target_pos
            changed = True

        if changed:
            state.dirty = True

    def copy_selected_source_range(
        start_pos: tuple[int, int],
        end_pos: tuple[int, int],
    ) -> bool:
        if not state.lines:
            return False

        start_line, start_col = start_pos
        end_line, end_col = end_pos
        if (end_line, end_col) < (start_line, start_col):
            start_line, start_col, end_line, end_col = end_line, end_col, start_line, start_col

        start_line = max(0, min(start_line, len(state.lines) - 1))
        end_line = max(0, min(end_line, len(state.lines) - 1))

        selected_parts: list[str] = []
        for idx in range(start_line, end_line + 1):
            plain = ANSI_ESCAPE_RE.sub("", state.lines[idx]).rstrip("\r\n")
            if idx == start_line and idx == end_line:
                left = max(0, min(start_col, len(plain)))
                right = max(left, min(end_col, len(plain)))
                selected_parts.append(plain[left:right])
            elif idx == start_line:
                left = max(0, min(start_col, len(plain)))
                selected_parts.append(plain[left:])
            elif idx == end_line:
                right = max(0, min(end_col, len(plain)))
                selected_parts.append(plain[:right])
            else:
                selected_parts.append(plain)

        selected_text = "\n".join(selected_parts)
        if not selected_text:
            fallback = ANSI_ESCAPE_RE.sub("", state.lines[start_line]).rstrip("\r\n")
            selected_text = fallback
        if not selected_text:
            return False
        return _copy_text_to_clipboard(selected_text)

    def handle_tree_mouse_wheel(mouse_key: str) -> bool:
        is_vertical = mouse_key.startswith("MOUSE_WHEEL_UP:") or mouse_key.startswith("MOUSE_WHEEL_DOWN:")
        is_horizontal = mouse_key.startswith("MOUSE_WHEEL_LEFT:") or mouse_key.startswith("MOUSE_WHEEL_RIGHT:")
        if not (is_vertical or is_horizontal):
            return False

        col, _row = parse_mouse_col_row(mouse_key)

        if is_horizontal:
            if state.browser_visible and col is not None and col <= state.left_width:
                return True
            prev_text_x = state.text_x
            step = 4
            if mouse_key.startswith("MOUSE_WHEEL_LEFT:"):
                state.text_x = max(0, state.text_x - step)
            else:
                state.text_x = min(max_horizontal_text_offset(), state.text_x + step)
            if state.text_x != prev_text_x:
                state.dirty = True
            return True

        direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
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
        nonlocal tree_watch_signature, source_selection_drag_active, source_selection_drag_pointer
        nonlocal source_selection_drag_edge, source_selection_drag_h_edge
        is_left_down = mouse_key.startswith("MOUSE_LEFT_DOWN:")
        is_left_up = mouse_key.startswith("MOUSE_LEFT_UP:")
        if not (is_left_down or is_left_up):
            return False

        col, row = parse_mouse_col_row(mouse_key)
        if col is None or row is None:
            return True

        def toggle_directory_entry(
            resolved: Path,
            *,
            content_mode_toggle: bool = False,
        ) -> None:
            nonlocal tree_watch_signature
            if content_mode_toggle and state.tree_filter_active and state.tree_filter_mode == "content":
                if resolved in state.tree_filter_collapsed_dirs:
                    state.tree_filter_collapsed_dirs.remove(resolved)
                    state.expanded.add(resolved)
                else:
                    if resolved != state.tree_root:
                        state.tree_filter_collapsed_dirs.add(resolved)
                    state.expanded.discard(resolved)
            else:
                state.expanded.symmetric_difference_update({resolved})
            rebuild_tree_entries(preferred_path=resolved)
            tree_watch_signature = None
            state.dirty = True

        if source_selection_drag_active and is_left_down:
            visible_rows = visible_content_rows()
            previous_row = source_selection_drag_pointer[1] if source_selection_drag_pointer is not None else row
            previous_col = source_selection_drag_pointer[0] if source_selection_drag_pointer is not None else col
            source_selection_drag_pointer = (col, row)
            if row < 1:
                source_selection_drag_edge = "top"
            elif row > visible_rows:
                source_selection_drag_edge = "bottom"
            elif row == 1 and (previous_row > row or source_selection_drag_edge == "top"):
                source_selection_drag_edge = "top"
            elif row == visible_rows and (
                previous_row < row or source_selection_drag_edge == "bottom"
            ):
                source_selection_drag_edge = "bottom"
            else:
                source_selection_drag_edge = None

            min_source_col, max_source_col = source_pane_col_bounds()
            if col < min_source_col:
                source_selection_drag_h_edge = "left"
            elif col > max_source_col:
                source_selection_drag_h_edge = "right"
            elif col == min_source_col and (previous_col > col or source_selection_drag_h_edge == "left"):
                source_selection_drag_h_edge = "left"
            elif col == max_source_col and (previous_col < col or source_selection_drag_h_edge == "right"):
                source_selection_drag_h_edge = "right"
            else:
                source_selection_drag_h_edge = None
            tick_source_selection_drag()
            return True

        selection_pos = source_selection_position(col, row)
        if selection_pos is not None:
            if is_left_down:
                if not source_selection_drag_active:
                    state.source_selection_anchor = selection_pos
                state.source_selection_focus = selection_pos
                source_selection_drag_active = True
                source_selection_drag_pointer = (col, row)
                source_selection_drag_edge = None
                source_selection_drag_h_edge = None
                state.dirty = True
                return True
            if state.source_selection_anchor is None:
                reset_source_selection_drag_state()
                return True
            state.source_selection_focus = selection_pos
            if state.source_selection_anchor == selection_pos and state.dir_preview_path is not None:
                preview_target = directory_preview_target_for_display_line(selection_pos[0])
                if preview_target is not None:
                    clear_source_selection()
                    reset_source_selection_drag_state()
                    jump_to_path_proxy(preview_target)
                    state.dirty = True
                    return True
            if state.source_selection_anchor == selection_pos:
                clicked_token = clicked_preview_search_token(selection_pos)
                if clicked_token is not None:
                    clear_source_selection()
                    reset_source_selection_drag_state()
                    return open_content_search_for_token(clicked_token)
            copy_selected_source_range(state.source_selection_anchor, selection_pos)
            reset_source_selection_drag_state()
            state.dirty = True
            return True

        if is_left_up:
            if source_selection_drag_active and state.source_selection_anchor is not None:
                source_selection_drag_pointer = (col, row)
                tick_source_selection_drag()
                end_pos = state.source_selection_focus or state.source_selection_anchor
                copy_selected_source_range(state.source_selection_anchor, end_pos)
                state.source_selection_focus = end_pos
                state.dirty = True
            reset_source_selection_drag_state()
            return True

        if source_selection_drag_active:
            # Keep live selection while dragging, even if pointer briefly leaves source pane.
            return True

        if clear_source_selection():
            state.dirty = True
        reset_source_selection_drag_state()

        if not (
            state.browser_visible
            and 1 <= row <= visible_content_rows()
            and col <= state.left_width
        ):
            return True

        query_row_visible = state.tree_filter_active
        if query_row_visible and row == 1:
            state.tree_filter_editing = True
            state.dirty = True
            return True

        raw_clicked_idx = state.tree_start + (row - 1 - (1 if query_row_visible else 0))
        if not (0 <= raw_clicked_idx < len(state.tree_entries)):
            return True

        raw_clicked_entry = state.tree_entries[raw_clicked_idx]
        raw_arrow_col = 1 + (raw_clicked_entry.depth * 2)
        if is_left_down and raw_clicked_entry.is_dir and raw_arrow_col <= col <= (raw_arrow_col + 1):
            resolved = raw_clicked_entry.path.resolve()
            toggle_directory_entry(resolved, content_mode_toggle=True)
            state.last_click_idx = -1
            state.last_click_time = 0.0
            return True

        clicked_idx = coerce_tree_filter_result_index(raw_clicked_idx)
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
            toggle_directory_entry(resolved)
            return True

        _copy_text_to_clipboard(entry.path.name)
        state.dirty = True
        return True

    def mark_tree_watch_dirty() -> None:
        nonlocal tree_watch_signature
        tree_watch_signature = None

    def sync_selected_target_after_tree_refresh(
        *,
        preferred_path: Path,
        force_rebuild: bool = False,
    ) -> None:
        previous_current_path = state.current_path.resolve()
        rebuild_tree_entries(preferred_path=preferred_path)
        if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries):
            selected_target = state.tree_entries[state.selected_idx].path.resolve()
        else:
            selected_target = state.tree_root.resolve()

        if selected_target == previous_current_path:
            refresh_rendered_for_current_path(
                reset_scroll=False,
                reset_dir_budget=False,
                force_rebuild=force_rebuild,
            )
        else:
            state.current_path = selected_target
            refresh_rendered_for_current_path(
                reset_scroll=True,
                reset_dir_budget=True,
                force_rebuild=force_rebuild,
            )
        schedule_tree_filter_index_warmup()
        refresh_git_status_overlay(force=True)
        state.dirty = True

    navigation_ops: NavigationPickerOps | None = None

    def current_jump_location_proxy():
        assert navigation_ops is not None
        return navigation_ops.current_jump_location()

    def record_jump_if_changed_proxy(origin):
        assert navigation_ops is not None
        return navigation_ops.record_jump_if_changed(origin)

    def jump_to_path_proxy(target: Path) -> None:
        assert navigation_ops is not None
        navigation_ops.jump_to_path(target)

    def jump_to_line_proxy(line_number: int) -> None:
        assert navigation_ops is not None
        navigation_ops.jump_to_line(line_number)

    tree_filter_ops = TreeFilterOps(
        state=state,
        visible_content_rows=visible_content_rows,
        rebuild_screen_lines=rebuild_screen_lines,
        preview_selected_entry=preview_selected_entry,
        current_jump_location=current_jump_location_proxy,
        record_jump_if_changed=record_jump_if_changed_proxy,
        jump_to_path=jump_to_path_proxy,
        jump_to_line=jump_to_line_proxy,
    )

    coerce_tree_filter_result_index = tree_filter_ops.coerce_tree_filter_result_index
    move_tree_selection = tree_filter_ops.move_tree_selection
    rebuild_tree_entries = tree_filter_ops.rebuild_tree_entries

    def apply_tree_filter_query(
        query: str,
        preview_selection: bool = False,
        select_first_file: bool = False,
    ) -> None:
        tree_filter_ops.apply_tree_filter_query(
            query,
            preview_selection=preview_selection,
            select_first_file=select_first_file,
        )
        sync_left_width_for_tree_filter_mode()

    def open_tree_filter(mode: str = "files") -> None:
        tree_filter_ops.open_tree_filter(mode)
        sync_left_width_for_tree_filter_mode()

    def close_tree_filter(clear_query: bool = True) -> None:
        tree_filter_ops.close_tree_filter(clear_query=clear_query)
        sync_left_width_for_tree_filter_mode()

    activate_tree_filter_selection = tree_filter_ops.activate_tree_filter_selection
    jump_to_next_content_hit = tree_filter_ops.jump_to_next_content_hit

    navigation_ops = NavigationPickerOps(
        state=state,
        command_palette_items=COMMAND_PALETTE_ITEMS,
        rebuild_screen_lines=rebuild_screen_lines,
        rebuild_tree_entries=rebuild_tree_entries,
        preview_selected_entry=preview_selected_entry,
        schedule_tree_filter_index_warmup=schedule_tree_filter_index_warmup,
        mark_tree_watch_dirty=mark_tree_watch_dirty,
        reset_git_watch_context=reset_git_watch_context,
        refresh_git_status_overlay=refresh_git_status_overlay,
        visible_content_rows=visible_content_rows,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
    )
    navigation_ops.set_open_tree_filter(open_tree_filter)

    current_jump_location = navigation_ops.current_jump_location
    record_jump_if_changed = navigation_ops.record_jump_if_changed

    def jump_to_next_git_modified(direction: int) -> bool:
        if direction == 0:
            return False

        if state.preview_is_git_diff and state.current_path.is_file():
            change_blocks = _git_change_block_start_lines(state.lines)
            if change_blocks:
                probe_line = state.start + max(0, visible_content_rows() // 3)
                current_block: int | None = None
                for line_idx in change_blocks:
                    if line_idx <= probe_line:
                        current_block = line_idx
                    else:
                        break

                target_line: int | None = None
                if direction > 0:
                    if current_block is None:
                        target_line = change_blocks[0]
                    else:
                        for line_idx in change_blocks:
                            if line_idx > current_block:
                                target_line = line_idx
                                break
                else:
                    if current_block is not None:
                        for line_idx in reversed(change_blocks):
                            if line_idx < current_block:
                                target_line = line_idx
                                break

                if target_line is not None:
                    next_start = _centered_scroll_start(
                        target_line,
                        state.max_start,
                        visible_content_rows(),
                    )
                    if next_start != state.start:
                        state.start = next_start
                        return True

        refresh_git_status_overlay()
        modified_paths = sorted_git_modified_file_paths()
        if not modified_paths:
            return False

        root = state.tree_root.resolve()
        if state.browser_visible and state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries):
            anchor_path = state.tree_entries[state.selected_idx].path.resolve()
        else:
            anchor_path = state.current_path.resolve()

        ordered_items: list[tuple[tuple[tuple[int, str, str], ...], Path]] = []
        for path in modified_paths:
            rel_path = path.relative_to(root)
            ordered_items.append((_tree_order_key_for_relative_path(rel_path), path))

        try:
            anchor_rel_path = anchor_path.relative_to(root)
            anchor_key: tuple[tuple[int, str, str], ...] | None = _tree_order_key_for_relative_path(
                anchor_rel_path,
                is_dir=anchor_path.is_dir(),
            )
        except Exception:
            anchor_key = None

        target: Path | None = None
        if direction > 0:
            if anchor_key is not None:
                for item_key, path in ordered_items:
                    if item_key > anchor_key:
                        target = path
                        break
            if target is None:
                target = ordered_items[0][1]
        else:
            if anchor_key is not None:
                for item_key, path in reversed(ordered_items):
                    if item_key < anchor_key:
                        target = path
                        break
            if target is None:
                target = ordered_items[-1][1]

        if target is None or target == anchor_path:
            return False

        origin = current_jump_location()
        navigation_ops.jump_to_path(target)
        record_jump_if_changed(origin)
        return True

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

    def launch_editor_for_path(target: Path) -> str | None:
        return launch_editor(target, terminal.disable_tui_mode, terminal.enable_tui_mode)

    def launch_lazygit() -> None:
        if shutil.which("lazygit") is None:
            show_inline_error("lazygit not found in PATH")
            return

        launch_error: str | None = None
        terminal.disable_tui_mode()
        try:
            try:
                subprocess.run(
                    ["lazygit"],
                    cwd=state.tree_root.resolve(),
                    check=False,
                )
            except Exception as exc:
                launch_error = f"failed to launch lazygit: {exc}"
        finally:
            terminal.enable_tui_mode()

        if launch_error is not None:
            show_inline_error(launch_error)
            return

        preferred_path = state.current_path.resolve()
        sync_selected_target_after_tree_refresh(preferred_path=preferred_path, force_rebuild=True)
        mark_tree_watch_dirty()

    normal_key_ops = NormalKeyOps(
        current_jump_location=current_jump_location,
        record_jump_if_changed=record_jump_if_changed,
        open_symbol_picker=navigation_ops.open_symbol_picker,
        reroot_to_parent=navigation_ops.reroot_to_parent,
        reroot_to_selected_target=navigation_ops.reroot_to_selected_target,
        toggle_hidden_files=navigation_ops.toggle_hidden_files,
        toggle_tree_pane=navigation_ops.toggle_tree_pane,
        toggle_wrap_mode=navigation_ops.toggle_wrap_mode,
        toggle_help_panel=navigation_ops.toggle_help_panel,
        toggle_git_features=toggle_git_features,
        launch_lazygit=launch_lazygit,
        handle_tree_mouse_wheel=handle_tree_mouse_wheel,
        handle_tree_mouse_click=handle_tree_mouse_click,
        move_tree_selection=move_tree_selection,
        rebuild_tree_entries=rebuild_tree_entries,
        preview_selected_entry=preview_selected_entry,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        refresh_git_status_overlay=refresh_git_status_overlay,
        maybe_grow_directory_preview=maybe_grow_directory_preview,
        visible_content_rows=visible_content_rows,
        rebuild_screen_lines=rebuild_screen_lines,
        mark_tree_watch_dirty=mark_tree_watch_dirty,
        launch_editor_for_path=launch_editor_for_path,
        jump_to_next_git_modified=jump_to_next_git_modified,
    )

    def handle_normal_key(key: str, term_columns: int) -> bool:
        return handle_normal_key_event(
            key=key,
            term_columns=term_columns,
            state=state,
            ops=normal_key_ops,
        )

    loop_timing = RuntimeLoopTiming(
        double_click_seconds=DOUBLE_CLICK_SECONDS,
        filter_cursor_blink_seconds=FILTER_CURSOR_BLINK_SECONDS,
        tree_filter_spinner_frame_seconds=TREE_FILTER_SPINNER_FRAME_SECONDS,
    )
    loop_callbacks = RuntimeLoopCallbacks(
        get_tree_filter_loading_until=tree_filter_ops.get_loading_until,
        tree_view_rows=tree_filter_ops.tree_view_rows,
        tree_filter_prompt_prefix=tree_filter_ops.tree_filter_prompt_prefix,
        tree_filter_placeholder=tree_filter_ops.tree_filter_placeholder,
        visible_content_rows=visible_content_rows,
        rebuild_screen_lines=rebuild_screen_lines,
        maybe_refresh_tree_watch=maybe_refresh_tree_watch,
        maybe_refresh_git_watch=maybe_refresh_git_watch,
        refresh_git_status_overlay=refresh_git_status_overlay,
        current_preview_image_path=current_preview_image_path,
        current_preview_image_geometry=current_preview_image_geometry,
        open_tree_filter=open_tree_filter,
        open_command_picker=navigation_ops.open_command_picker,
        close_picker=navigation_ops.close_picker,
        refresh_command_picker_matches=navigation_ops.refresh_command_picker_matches,
        activate_picker_selection=navigation_ops.activate_picker_selection,
        refresh_active_picker_matches=navigation_ops.refresh_active_picker_matches,
        handle_tree_mouse_wheel=handle_tree_mouse_wheel,
        handle_tree_mouse_click=handle_tree_mouse_click,
        toggle_help_panel=navigation_ops.toggle_help_panel,
        close_tree_filter=close_tree_filter,
        activate_tree_filter_selection=activate_tree_filter_selection,
        move_tree_selection=move_tree_selection,
        apply_tree_filter_query=apply_tree_filter_query,
        jump_to_next_content_hit=jump_to_next_content_hit,
        set_named_mark=navigation_ops.set_named_mark,
        jump_to_named_mark=navigation_ops.jump_to_named_mark,
        jump_back_in_history=navigation_ops.jump_back_in_history,
        jump_forward_in_history=navigation_ops.jump_forward_in_history,
        handle_normal_key=handle_normal_key,
        save_left_pane_width=save_left_pane_width_for_mode,
        tick_source_selection_drag=tick_source_selection_drag,
    )

    run_main_loop(
        state=state,
        terminal=terminal,
        stdin_fd=stdin_fd,
        timing=loop_timing,
        callbacks=loop_callbacks,
    )
