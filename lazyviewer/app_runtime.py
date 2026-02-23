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
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
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
WRAP_STATUS_SECONDS = 1.2
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


def _parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
    parts = mouse_key.split(":")
    if len(parts) < 3:
        return None, None
    try:
        return int(parts[1]), int(parts[2])
    except Exception:
        return None, None


def _rendered_line_display_width(line: str) -> int:
    plain = ANSI_ESCAPE_RE.sub("", line).rstrip("\r\n")
    col = 0
    for ch in plain:
        col += char_display_width(ch, col)
    return col


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


def _clicked_preview_search_token(
    lines: list[str],
    selection_pos: tuple[int, int],
) -> str | None:
    if not lines:
        return None

    line_idx, text_col = selection_pos
    if line_idx < 0 or line_idx >= len(lines):
        return None

    plain_line = ANSI_ESCAPE_RE.sub("", lines[line_idx]).rstrip("\r\n")
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


def _open_content_search_for_token(
    *,
    state: AppState,
    query: str,
    open_tree_filter: Callable[[str], None],
    apply_tree_filter_query: Callable[..., None],
) -> bool:
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


def _clear_status_message(state: AppState) -> None:
    state.status_message = ""
    state.status_message_until = 0.0


def _set_status_message(state: AppState, message: str) -> None:
    state.status_message = message
    state.status_message_until = time.monotonic() + WRAP_STATUS_SECONDS


class _TreeFilterIndexWarmupScheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: tuple[Path, bool] | None = None
        self._running = False

    def _worker(self) -> None:
        while True:
            with self._lock:
                pending = self._pending
                self._pending = None
                if pending is None:
                    self._running = False
                    return

            root, show_hidden = pending
            try:
                collect_project_file_labels(
                    root,
                    show_hidden,
                    skip_gitignored=_skip_gitignored_for_hidden_mode(show_hidden),
                )
            except Exception:
                # Warming is best-effort; foreground path still loads synchronously if needed.
                pass

    def schedule(self, root: Path, show_hidden: bool) -> None:
        with self._lock:
            self._pending = (root.resolve(), show_hidden)
            if self._running:
                return
            self._running = True

        worker = threading.Thread(
            target=self._worker,
            name="lazyviewer-file-index",
            daemon=True,
        )
        worker.start()

    def schedule_for_state(
        self,
        state: AppState,
        root: Path | None = None,
        show_hidden_value: bool | None = None,
    ) -> None:
        target_root = state.tree_root if root is None else root
        target_show_hidden = state.show_hidden if show_hidden_value is None else show_hidden_value
        self.schedule(target_root, target_show_hidden)


class _PagerLayoutOps:
    def __init__(
        self,
        *,
        state: AppState,
        kitty_graphics_supported: bool,
    ) -> None:
        self.state = state
        self.kitty_graphics_supported = kitty_graphics_supported
        self.content_mode_left_width_active = self.content_search_match_view_active()

    def effective_text_width(self, columns: int | None = None) -> int:
        if columns is None:
            columns = shutil.get_terminal_size((80, 24)).columns
        if self.state.browser_visible:
            return max(1, columns - self.state.left_width - 2)
        return max(1, columns - 1)

    def visible_content_rows(self) -> int:
        help_rows = help_panel_row_count(
            self.state.usable,
            self.state.show_help,
            browser_visible=self.state.browser_visible,
            tree_filter_active=self.state.tree_filter_active,
            tree_filter_mode=self.state.tree_filter_mode,
            tree_filter_editing=self.state.tree_filter_editing,
        )
        return max(1, self.state.usable - help_rows)

    def content_search_match_view_active(self) -> bool:
        return (
            self.state.tree_filter_active
            and self.state.tree_filter_mode == "content"
            and bool(self.state.tree_filter_query)
        )

    def rebuild_screen_lines(
        self,
        columns: int | None = None,
        preserve_scroll: bool = True,
    ) -> None:
        self.state.lines = build_screen_lines(
            self.state.rendered,
            self.effective_text_width(columns),
            wrap=self.state.wrap_text,
        )
        self.state.max_start = max(0, len(self.state.lines) - self.visible_content_rows())
        if preserve_scroll:
            self.state.start = max(0, min(self.state.start, self.state.max_start))
        else:
            self.state.start = 0
        if self.state.wrap_text:
            self.state.text_x = 0

    def sync_left_width_for_tree_filter_mode(self, force: bool = False) -> None:
        use_content_mode_width = self.content_search_match_view_active()
        if not force and use_content_mode_width == self.content_mode_left_width_active:
            return
        self.content_mode_left_width_active = use_content_mode_width

        columns = shutil.get_terminal_size((80, 24)).columns
        if use_content_mode_width:
            saved_percent = load_content_search_left_pane_percent()
            if saved_percent is None:
                current_percent = (self.state.left_width / max(1, columns)) * 100.0
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
        if desired_left == self.state.left_width:
            return

        self.state.left_width = desired_left
        self.state.right_width = max(1, columns - self.state.left_width - 2)
        if self.state.right_width != self.state.last_right_width:
            self.state.last_right_width = self.state.right_width
            self.rebuild_screen_lines(columns=columns)
        self.state.dirty = True

    def save_left_pane_width_for_mode(self, total_width: int, left_width: int) -> None:
        if self.content_search_match_view_active():
            save_content_search_left_pane_percent(total_width, left_width)
            return
        save_left_pane_percent(total_width, left_width)

    def show_inline_error(self, message: str) -> None:
        self.state.rendered = f"\033[31m{message}\033[0m"
        self.rebuild_screen_lines(preserve_scroll=False)
        self.state.text_x = 0
        self.state.dir_preview_path = None
        self.state.dir_preview_truncated = False
        self.state.preview_image_path = None
        self.state.preview_image_format = None
        self.state.dirty = True

    def current_preview_image_path(self) -> Path | None:
        if not self.kitty_graphics_supported:
            return None
        if self.state.preview_image_format != "png":
            return None
        if self.state.preview_image_path is None:
            return None
        try:
            image_path = self.state.preview_image_path.resolve()
        except Exception:
            image_path = self.state.preview_image_path
        if not image_path.exists() or not image_path.is_file():
            return None
        return image_path

    def current_preview_image_geometry(self, columns: int) -> tuple[int, int, int, int]:
        image_rows = self.visible_content_rows()
        if self.state.browser_visible:
            image_col = self.state.left_width + 2
            image_width = max(1, columns - self.state.left_width - 2 - 1)
        else:
            image_col = 1
            image_width = max(1, columns - 1)
        return image_col, 1, image_width, image_rows


class _SourcePaneOps:
    def __init__(
        self,
        *,
        state: AppState,
        visible_content_rows: Callable[[], int],
    ) -> None:
        self.state = state
        self.visible_content_rows = visible_content_rows

    def preview_pane_width(self) -> int:
        if self.state.browser_visible:
            return max(1, self.state.right_width)
        term = shutil.get_terminal_size((80, 24))
        return max(1, term.columns - 1)

    def max_horizontal_text_offset(self) -> int:
        if self.state.wrap_text or not self.state.lines:
            return 0
        viewport_width = self.preview_pane_width()
        max_width = 0
        for line in self.state.lines:
            max_width = max(max_width, _rendered_line_display_width(line))
        return max(0, max_width - viewport_width)

    def source_pane_col_bounds(self) -> tuple[int, int]:
        if self.state.browser_visible:
            min_col = self.state.left_width + 2
            pane_width = max(1, self.state.right_width)
        else:
            min_col = 1
            pane_width = self.preview_pane_width()
        max_col = min_col + pane_width - 1
        return min_col, max_col

    def source_selection_position(self, col: int, row: int) -> tuple[int, int] | None:
        visible_rows = self.visible_content_rows()
        if row < 1 or row > visible_rows:
            return None

        if self.state.browser_visible:
            right_start_col = self.state.left_width + 2
            if col < right_start_col:
                return None
            text_col = max(0, col - right_start_col + self.state.text_x)
        else:
            right_start_col = 1
            if col < right_start_col:
                return None
            text_col = max(0, col - right_start_col + self.state.text_x)

        if not self.state.lines:
            return None
        line_idx = max(0, min(self.state.start + row - 1, len(self.state.lines) - 1))
        return line_idx, text_col

    def display_line_to_source_line(self, display_idx: int) -> int | None:
        if display_idx < 0 or display_idx >= len(self.state.lines):
            return None
        if not self.state.wrap_text:
            return display_idx

        source_idx = 0
        for idx in range(display_idx):
            if _line_has_newline_terminator(self.state.lines[idx]):
                source_idx += 1
        return source_idx

    def directory_preview_target_for_display_line(self, display_idx: int) -> Path | None:
        if self.state.dir_preview_path is None:
            return None

        source_idx = self.display_line_to_source_line(display_idx)
        if source_idx is None:
            return None

        rendered_lines = self.state.rendered.splitlines()
        if source_idx < 0 or source_idx >= len(rendered_lines):
            return None

        root = self.state.dir_preview_path.resolve()
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

            if target is not None and is_dir:
                dirs_by_depth[depth] = target
                for existing_depth in list(dirs_by_depth):
                    if existing_depth > depth:
                        del dirs_by_depth[existing_depth]

            if idx == source_idx:
                if target is None:
                    return None
                return target

        return None


def _launch_editor_for_path(target: Path, *, terminal: TerminalController) -> str | None:
    return launch_editor(target, terminal.disable_tui_mode, terminal.enable_tui_mode)


def _dispatch_normal_key(
    key: str,
    term_columns: int,
    *,
    state: AppState,
    ops: NormalKeyOps,
) -> bool:
    return handle_normal_key_event(
        key=key,
        term_columns=term_columns,
        state=state,
        ops=ops,
    )


def _clear_source_selection(state: AppState) -> bool:
    changed = state.source_selection_anchor is not None or state.source_selection_focus is not None
    state.source_selection_anchor = None
    state.source_selection_focus = None
    return changed


def _sorted_git_modified_file_paths(state: AppState) -> list[Path]:
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


def _refresh_rendered_for_current_path(
    *,
    state: AppState,
    style: str,
    no_color: bool,
    rebuild_screen_lines: Callable[..., None],
    visible_content_rows: Callable[[], int],
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


def _maybe_grow_directory_preview(
    *,
    state: AppState,
    visible_content_rows: Callable[[], int],
    refresh_rendered_for_current_path: Callable[..., None],
) -> bool:
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


def _toggle_git_features(
    *,
    state: AppState,
    refresh_git_status_overlay: Callable[..., None],
    refresh_rendered_for_current_path: Callable[..., None],
) -> None:
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


def _preview_selected_entry(
    *,
    state: AppState,
    clear_source_selection: Callable[[], bool],
    refresh_rendered_for_current_path: Callable[..., None],
    jump_to_line: Callable[[int], None],
    force: bool = False,
) -> None:
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
            jump_to_line(max(0, entry.line - 1))
        return
    if not force and selected_target == state.current_path.resolve():
        return
    state.current_path = selected_target
    refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)


def _sync_selected_target_after_tree_refresh(
    *,
    state: AppState,
    rebuild_tree_entries: Callable[..., None],
    refresh_rendered_for_current_path: Callable[..., None],
    schedule_tree_filter_index_warmup: Callable[..., None],
    refresh_git_status_overlay: Callable[..., None],
    preferred_path: Path,
    force_rebuild: bool = False,
) -> None:
    previous_current_path = state.current_path.resolve()
    rebuild_tree_entries(preferred_path=preferred_path)
    if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries):
        selected_target = state.tree_entries[state.selected_idx].path.resolve()
    else:
        selected_target = state.tree_root.resolve()

    changed_target = selected_target != previous_current_path
    if changed_target:
        state.current_path = selected_target
    refresh_rendered_for_current_path(
        reset_scroll=changed_target,
        reset_dir_budget=changed_target,
        force_rebuild=force_rebuild,
    )
    schedule_tree_filter_index_warmup()
    refresh_git_status_overlay(force=True)
    state.dirty = True


def _jump_to_next_git_modified(
    direction: int,
    *,
    state: AppState,
    visible_content_rows: Callable[[], int],
    refresh_git_status_overlay: Callable[..., None],
    sorted_git_modified_file_paths: Callable[[], list[Path]],
    current_jump_location: Callable[[], object],
    jump_to_path: Callable[[Path], None],
    record_jump_if_changed: Callable[[object], None],
) -> bool:
    if direction == 0:
        return False
    _clear_status_message(state)

    same_file_change_blocks: list[int] = []
    if state.preview_is_git_diff and state.current_path.is_file():
        same_file_change_blocks = _git_change_block_start_lines(state.lines)
        if same_file_change_blocks:
            probe_line = state.start + max(0, visible_content_rows() // 3)
            current_block: int | None = None
            for line_idx in same_file_change_blocks:
                if line_idx <= probe_line:
                    current_block = line_idx
                else:
                    break

            target_line: int | None = None
            if direction > 0:
                if current_block is None:
                    target_line = same_file_change_blocks[0]
                else:
                    for line_idx in same_file_change_blocks:
                        if line_idx > current_block:
                            target_line = line_idx
                            break
            else:
                if current_block is not None:
                    for line_idx in reversed(same_file_change_blocks):
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
    wrapped_files = False
    if direction > 0:
        if anchor_key is not None:
            for item_key, path in ordered_items:
                if item_key > anchor_key:
                    target = path
                    break
            if target is None:
                wrapped_files = True
        if target is None:
            target = ordered_items[0][1]
    else:
        if anchor_key is not None:
            for item_key, path in reversed(ordered_items):
                if item_key < anchor_key:
                    target = path
                    break
            if target is None:
                wrapped_files = True
        if target is None:
            target = ordered_items[-1][1]

    if target is None:
        return False

    if target == anchor_path and same_file_change_blocks:
        wrap_line = same_file_change_blocks[0] if direction > 0 else same_file_change_blocks[-1]
        next_start = _centered_scroll_start(
            wrap_line,
            state.max_start,
            visible_content_rows(),
        )
        state.start = next_start
        _set_status_message(
            state,
            "wrapped to first change" if direction > 0 else "wrapped to last change",
        )
        return True

    if target == anchor_path:
        return False

    origin = current_jump_location()
    jump_to_path(target)
    record_jump_if_changed(origin)
    if wrapped_files:
        _set_status_message(
            state,
            "wrapped to first change" if direction > 0 else "wrapped to last change",
    )
    return True


def _copy_selected_source_range(
    *,
    state: AppState,
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


def _handle_tree_mouse_wheel(
    mouse_key: str,
    *,
    state: AppState,
    move_tree_selection: Callable[[int], bool],
    maybe_grow_directory_preview: Callable[[], bool],
    max_horizontal_text_offset: Callable[[], int],
) -> bool:
    is_vertical = mouse_key.startswith("MOUSE_WHEEL_UP:") or mouse_key.startswith("MOUSE_WHEEL_DOWN:")
    is_horizontal = mouse_key.startswith("MOUSE_WHEEL_LEFT:") or mouse_key.startswith("MOUSE_WHEEL_RIGHT:")
    if not (is_vertical or is_horizontal):
        return False

    col, _row = _parse_mouse_col_row(mouse_key)
    in_tree_pane = state.browser_visible and col is not None and col <= state.left_width

    if is_horizontal:
        if in_tree_pane:
            return True
        prev_text_x = state.text_x
        if mouse_key.startswith("MOUSE_WHEEL_LEFT:"):
            state.text_x = max(0, state.text_x - 4)
        else:
            state.text_x = min(max_horizontal_text_offset(), state.text_x + 4)
        if state.text_x != prev_text_x:
            state.dirty = True
        return True

    direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
    if in_tree_pane:
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


@dataclass
class _SourceSelectionDragState:
    active: bool = False
    pointer: tuple[int, int] | None = None
    vertical_edge: str | None = None
    horizontal_edge: str | None = None

    def reset(self) -> None:
        self.active = False
        self.pointer = None
        self.vertical_edge = None
        self.horizontal_edge = None


class _TreeMouseHandlers:
    def __init__(
        self,
        *,
        state: AppState,
        visible_content_rows: Callable[[], int],
        source_pane_col_bounds: Callable[[], tuple[int, int]],
        source_selection_position: Callable[[int, int], tuple[int, int] | None],
        directory_preview_target_for_display_line: Callable[[int], Path | None],
        max_horizontal_text_offset: Callable[[], int],
        maybe_grow_directory_preview: Callable[[], bool],
        clear_source_selection: Callable[[], bool],
        copy_selected_source_range: Callable[..., bool],
        rebuild_tree_entries: Callable[..., None],
        mark_tree_watch_dirty: Callable[[], None],
        coerce_tree_filter_result_index: Callable[[int], int | None],
        preview_selected_entry: Callable[..., None],
        activate_tree_filter_selection: Callable[[], None],
        open_tree_filter: Callable[[str], bool],
        apply_tree_filter_query: Callable[..., None],
        jump_to_path: Callable[[Path], None],
    ) -> None:
        self._state = state
        self._visible_content_rows = visible_content_rows
        self._source_pane_col_bounds = source_pane_col_bounds
        self._source_selection_position = source_selection_position
        self._directory_preview_target_for_display_line = directory_preview_target_for_display_line
        self._max_horizontal_text_offset = max_horizontal_text_offset
        self._maybe_grow_directory_preview = maybe_grow_directory_preview
        self._clear_source_selection = clear_source_selection
        self._copy_selected_source_range = copy_selected_source_range
        self._rebuild_tree_entries = rebuild_tree_entries
        self._mark_tree_watch_dirty = mark_tree_watch_dirty
        self._coerce_tree_filter_result_index = coerce_tree_filter_result_index
        self._preview_selected_entry = preview_selected_entry
        self._activate_tree_filter_selection = activate_tree_filter_selection
        self._open_tree_filter = open_tree_filter
        self._apply_tree_filter_query = apply_tree_filter_query
        self._jump_to_path = jump_to_path
        self._drag = _SourceSelectionDragState()

    def _reset_source_selection_drag_state(self) -> None:
        self._drag.reset()

    def _update_drag_pointer(self, col: int, row: int) -> None:
        visible_rows = self._visible_content_rows()
        previous_row = self._drag.pointer[1] if self._drag.pointer is not None else row
        previous_col = self._drag.pointer[0] if self._drag.pointer is not None else col
        self._drag.pointer = (col, row)

        if row < 1:
            self._drag.vertical_edge = "top"
        elif row > visible_rows:
            self._drag.vertical_edge = "bottom"
        elif row == 1 and (previous_row > row or self._drag.vertical_edge == "top"):
            self._drag.vertical_edge = "top"
        elif row == visible_rows and (previous_row < row or self._drag.vertical_edge == "bottom"):
            self._drag.vertical_edge = "bottom"
        else:
            self._drag.vertical_edge = None

        min_source_col, max_source_col = self._source_pane_col_bounds()
        if col < min_source_col:
            self._drag.horizontal_edge = "left"
        elif col > max_source_col:
            self._drag.horizontal_edge = "right"
        elif col == min_source_col and (previous_col > col or self._drag.horizontal_edge == "left"):
            self._drag.horizontal_edge = "left"
        elif col == max_source_col and (previous_col < col or self._drag.horizontal_edge == "right"):
            self._drag.horizontal_edge = "right"
        else:
            self._drag.horizontal_edge = None

    def tick_source_selection_drag(self) -> None:
        state = self._state
        if not self._drag.active or state.source_selection_anchor is None:
            return
        if self._drag.pointer is None:
            return

        col, row = self._drag.pointer
        visible_rows = self._visible_content_rows()
        if visible_rows <= 0:
            return

        min_source_col, max_source_col = self._source_pane_col_bounds()
        target_col = max(min_source_col, min(col, max_source_col))
        changed = False

        top_edge_active = row < 1 or (row == 1 and self._drag.vertical_edge == "top")
        bottom_edge_active = row > visible_rows or (row == visible_rows and self._drag.vertical_edge == "bottom")
        left_edge_active = col < min_source_col or (col == min_source_col and self._drag.horizontal_edge == "left")
        right_edge_active = col > max_source_col or (
            col == max_source_col and self._drag.horizontal_edge == "right"
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
                grew_preview = self._maybe_grow_directory_preview()
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
            state.text_x = min(self._max_horizontal_text_offset(), state.text_x + step)
            if state.text_x != previous_text_x:
                changed = True

        target_pos = self._source_selection_position(target_col, target_row)
        if target_pos is not None and target_pos != state.source_selection_focus:
            state.source_selection_focus = target_pos
            changed = True

        if changed:
            state.dirty = True

    def _toggle_directory_entry(
        self,
        resolved: Path,
        *,
        content_mode_toggle: bool = False,
    ) -> None:
        state = self._state
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
        self._rebuild_tree_entries(preferred_path=resolved)
        self._mark_tree_watch_dirty()
        state.dirty = True

    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        state = self._state
        is_left_down = mouse_key.startswith("MOUSE_LEFT_DOWN:")
        is_left_up = mouse_key.startswith("MOUSE_LEFT_UP:")
        if not (is_left_down or is_left_up):
            return False

        col, row = _parse_mouse_col_row(mouse_key)
        if col is None or row is None:
            return True

        if self._drag.active and is_left_down:
            self._update_drag_pointer(col, row)
            self.tick_source_selection_drag()
            return True

        selection_pos = self._source_selection_position(col, row)
        if selection_pos is not None:
            if is_left_down:
                if not self._drag.active:
                    state.source_selection_anchor = selection_pos
                state.source_selection_focus = selection_pos
                self._drag.active = True
                self._drag.pointer = (col, row)
                self._drag.vertical_edge = None
                self._drag.horizontal_edge = None
                state.dirty = True
                return True
            if state.source_selection_anchor is None:
                self._reset_source_selection_drag_state()
                return True
            state.source_selection_focus = selection_pos
            same_selection_pos = state.source_selection_anchor == selection_pos
            if same_selection_pos and state.dir_preview_path is not None:
                preview_target = self._directory_preview_target_for_display_line(selection_pos[0])
                if preview_target is not None:
                    self._clear_source_selection()
                    self._reset_source_selection_drag_state()
                    self._jump_to_path(preview_target)
                    state.dirty = True
                    return True
            if same_selection_pos:
                clicked_token = _clicked_preview_search_token(state.lines, selection_pos)
                if clicked_token is not None:
                    self._clear_source_selection()
                    self._reset_source_selection_drag_state()
                    return _open_content_search_for_token(
                        state=state,
                        query=clicked_token,
                        open_tree_filter=self._open_tree_filter,
                        apply_tree_filter_query=self._apply_tree_filter_query,
                    )
            self._copy_selected_source_range(start_pos=state.source_selection_anchor, end_pos=selection_pos)
            self._reset_source_selection_drag_state()
            state.dirty = True
            return True

        if is_left_up:
            if self._drag.active and state.source_selection_anchor is not None:
                self._drag.pointer = (col, row)
                self.tick_source_selection_drag()
                end_pos = state.source_selection_focus or state.source_selection_anchor
                self._copy_selected_source_range(start_pos=state.source_selection_anchor, end_pos=end_pos)
                state.source_selection_focus = end_pos
                state.dirty = True
            self._reset_source_selection_drag_state()
            return True

        if self._drag.active:
            # Keep live selection while dragging, even if pointer briefly leaves source pane.
            return True

        if self._clear_source_selection():
            state.dirty = True
        self._reset_source_selection_drag_state()

        if not (state.browser_visible and 1 <= row <= self._visible_content_rows() and col <= state.left_width):
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
            self._toggle_directory_entry(resolved, content_mode_toggle=True)
            state.last_click_idx = -1
            state.last_click_time = 0.0
            return True

        clicked_idx = self._coerce_tree_filter_result_index(raw_clicked_idx)
        if clicked_idx is None:
            return True

        prev_selected = state.selected_idx
        state.selected_idx = clicked_idx
        self._preview_selected_entry()
        if state.selected_idx != prev_selected:
            state.dirty = True

        now = time.monotonic()
        is_double = clicked_idx == state.last_click_idx and (now - state.last_click_time) <= DOUBLE_CLICK_SECONDS
        state.last_click_idx = clicked_idx
        state.last_click_time = now
        if not is_double:
            return True

        if state.tree_filter_active and state.tree_filter_query:
            self._activate_tree_filter_selection()
            return True

        entry = state.tree_entries[state.selected_idx]
        if entry.is_dir:
            resolved = entry.path.resolve()
            self._toggle_directory_entry(resolved)
            return True

        _copy_text_to_clipboard(entry.path.name)
        state.dirty = True
        return True


def _launch_lazygit(
    *,
    state: AppState,
    terminal: TerminalController,
    show_inline_error: Callable[[str], None],
    sync_selected_target_after_tree_refresh: Callable[..., None],
    mark_tree_watch_dirty: Callable[[], None],
) -> None:
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


@dataclass
class _WatchRefreshContext:
    tree_last_poll: float = 0.0
    tree_signature: str | None = None
    git_last_poll: float = 0.0
    git_signature: str | None = None
    git_repo_root: Path | None = None
    git_dir: Path | None = None


class _NavigationProxy:
    def __init__(self) -> None:
        self._ops: NavigationPickerOps | None = None

    def bind(self, ops: NavigationPickerOps) -> None:
        self._ops = ops

    def current_jump_location(self):
        assert self._ops is not None
        return self._ops.current_jump_location()

    def record_jump_if_changed(self, origin: object) -> None:
        assert self._ops is not None
        self._ops.record_jump_if_changed(origin)

    def jump_to_path(self, target: Path) -> None:
        assert self._ops is not None
        self._ops.jump_to_path(target)

    def jump_to_line(self, line_number: int) -> None:
        assert self._ops is not None
        self._ops.jump_to_line(line_number)


def _refresh_git_status_overlay(
    *,
    state: AppState,
    refresh_rendered_for_current_path: Callable[..., None],
    force: bool = False,
) -> None:
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


def _reset_git_watch_context(
    *,
    state: AppState,
    watch_context: _WatchRefreshContext,
) -> None:
    watch_context.git_repo_root, watch_context.git_dir = resolve_git_paths(state.tree_root)
    watch_context.git_last_poll = 0.0
    watch_context.git_signature = None


def _maybe_refresh_tree_watch(
    *,
    state: AppState,
    watch_context: _WatchRefreshContext,
    sync_selected_target_after_tree_refresh: Callable[..., None],
) -> None:
    now = time.monotonic()
    if (now - watch_context.tree_last_poll) < TREE_WATCH_POLL_SECONDS:
        return
    watch_context.tree_last_poll = now

    signature = build_tree_watch_signature(
        state.tree_root,
        state.expanded,
        state.show_hidden,
    )
    if watch_context.tree_signature is None:
        watch_context.tree_signature = signature
        return
    if signature == watch_context.tree_signature:
        return

    watch_context.tree_signature = signature
    preferred_path = (
        state.tree_entries[state.selected_idx].path.resolve()
        if state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries)
        else state.current_path.resolve()
    )
    sync_selected_target_after_tree_refresh(preferred_path=preferred_path)


def _maybe_refresh_git_watch(
    *,
    state: AppState,
    watch_context: _WatchRefreshContext,
    refresh_git_status_overlay: Callable[..., None],
    refresh_rendered_for_current_path: Callable[..., None],
) -> None:
    if not state.git_features_enabled:
        return
    now = time.monotonic()
    if (now - watch_context.git_last_poll) < GIT_WATCH_POLL_SECONDS:
        return
    watch_context.git_last_poll = now

    signature = build_git_watch_signature(watch_context.git_dir)
    if watch_context.git_signature is None:
        watch_context.git_signature = signature
        return
    if signature == watch_context.git_signature:
        return

    watch_context.git_signature = signature
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


def _mark_tree_watch_dirty(watch_context: _WatchRefreshContext) -> None:
    watch_context.tree_signature = None


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
    selected_idx = next(
        (
            idx
            for idx, entry in enumerate(tree_entries)
            if entry.path.resolve() == selected_path.resolve()
        ),
        0,
    )

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
    index_warmup_scheduler = _TreeFilterIndexWarmupScheduler()
    schedule_tree_filter_index_warmup = partial(index_warmup_scheduler.schedule_for_state, state)
    layout_ops = _PagerLayoutOps(
        state=state,
        kitty_graphics_supported=kitty_graphics_supported,
    )
    visible_content_rows = layout_ops.visible_content_rows
    sync_left_width_for_tree_filter_mode = layout_ops.sync_left_width_for_tree_filter_mode
    save_left_pane_width_for_mode = layout_ops.save_left_pane_width_for_mode
    rebuild_screen_lines = layout_ops.rebuild_screen_lines
    show_inline_error = layout_ops.show_inline_error
    current_preview_image_path = layout_ops.current_preview_image_path
    current_preview_image_geometry = layout_ops.current_preview_image_geometry
    watch_refresh = _WatchRefreshContext()
    mark_tree_watch_dirty = partial(_mark_tree_watch_dirty, watch_refresh)

    sorted_git_modified_file_paths = partial(_sorted_git_modified_file_paths, state)
    refresh_rendered_for_current_path = partial(
        _refresh_rendered_for_current_path,
        state=state,
        style=style,
        no_color=no_color,
        rebuild_screen_lines=rebuild_screen_lines,
        visible_content_rows=visible_content_rows,
    )

    refresh_git_status_overlay = partial(
        _refresh_git_status_overlay,
        state=state,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
    )
    reset_git_watch_context = partial(
        _reset_git_watch_context,
        state=state,
        watch_context=watch_refresh,
    )
    maybe_refresh_tree_watch: Callable[[], None]
    maybe_refresh_git_watch = partial(
        _maybe_refresh_git_watch,
        state=state,
        watch_context=watch_refresh,
        refresh_git_status_overlay=refresh_git_status_overlay,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
    )

    clear_source_selection = partial(_clear_source_selection, state)
    toggle_git_features = partial(
        _toggle_git_features,
        state=state,
        refresh_git_status_overlay=refresh_git_status_overlay,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
    )
    preview_selected_entry: Callable[..., None]

    maybe_grow_directory_preview = partial(
        _maybe_grow_directory_preview,
        state=state,
        visible_content_rows=visible_content_rows,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
    )

    source_pane_ops = _SourcePaneOps(
        state=state,
        visible_content_rows=visible_content_rows,
    )
    preview_pane_width = source_pane_ops.preview_pane_width
    max_horizontal_text_offset = source_pane_ops.max_horizontal_text_offset
    source_pane_col_bounds = source_pane_ops.source_pane_col_bounds
    source_selection_position = source_pane_ops.source_selection_position
    display_line_to_source_line = source_pane_ops.display_line_to_source_line
    directory_preview_target_for_display_line = source_pane_ops.directory_preview_target_for_display_line
    copy_selected_source_range = partial(_copy_selected_source_range, state=state)
    handle_tree_mouse_wheel: Callable[[str], bool]
    handle_tree_mouse_click: Callable[[str], bool]
    tick_source_selection_drag: Callable[[], None]

    sync_selected_target_after_tree_refresh: Callable[..., None]
    navigation_proxy = _NavigationProxy()

    preview_selected_entry = partial(
        _preview_selected_entry,
        state=state,
        clear_source_selection=clear_source_selection,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        jump_to_line=navigation_proxy.jump_to_line,
    )

    tree_filter_ops = TreeFilterOps(
        state=state,
        visible_content_rows=visible_content_rows,
        rebuild_screen_lines=rebuild_screen_lines,
        preview_selected_entry=preview_selected_entry,
        current_jump_location=navigation_proxy.current_jump_location,
        record_jump_if_changed=navigation_proxy.record_jump_if_changed,
        jump_to_path=navigation_proxy.jump_to_path,
        jump_to_line=navigation_proxy.jump_to_line,
        on_tree_filter_state_change=sync_left_width_for_tree_filter_mode,
    )

    coerce_tree_filter_result_index = tree_filter_ops.coerce_tree_filter_result_index
    move_tree_selection = tree_filter_ops.move_tree_selection
    rebuild_tree_entries = tree_filter_ops.rebuild_tree_entries
    apply_tree_filter_query = tree_filter_ops.apply_tree_filter_query
    open_tree_filter = tree_filter_ops.open_tree_filter
    close_tree_filter = tree_filter_ops.close_tree_filter
    activate_tree_filter_selection = tree_filter_ops.activate_tree_filter_selection
    jump_to_next_content_hit = tree_filter_ops.jump_to_next_content_hit
    sync_selected_target_after_tree_refresh = partial(
        _sync_selected_target_after_tree_refresh,
        state=state,
        rebuild_tree_entries=rebuild_tree_entries,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        schedule_tree_filter_index_warmup=schedule_tree_filter_index_warmup,
        refresh_git_status_overlay=refresh_git_status_overlay,
    )
    maybe_refresh_tree_watch = partial(
        _maybe_refresh_tree_watch,
        state=state,
        watch_context=watch_refresh,
        sync_selected_target_after_tree_refresh=sync_selected_target_after_tree_refresh,
    )
    handle_tree_mouse_wheel = partial(
        _handle_tree_mouse_wheel,
        state=state,
        move_tree_selection=move_tree_selection,
        maybe_grow_directory_preview=maybe_grow_directory_preview,
        max_horizontal_text_offset=max_horizontal_text_offset,
    )

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
    navigation_proxy.bind(navigation_ops)
    navigation_ops.set_open_tree_filter(open_tree_filter)
    mouse_handlers = _TreeMouseHandlers(
        state=state,
        visible_content_rows=visible_content_rows,
        source_pane_col_bounds=source_pane_col_bounds,
        source_selection_position=source_selection_position,
        directory_preview_target_for_display_line=directory_preview_target_for_display_line,
        max_horizontal_text_offset=max_horizontal_text_offset,
        maybe_grow_directory_preview=maybe_grow_directory_preview,
        clear_source_selection=clear_source_selection,
        copy_selected_source_range=copy_selected_source_range,
        rebuild_tree_entries=rebuild_tree_entries,
        mark_tree_watch_dirty=mark_tree_watch_dirty,
        coerce_tree_filter_result_index=coerce_tree_filter_result_index,
        preview_selected_entry=preview_selected_entry,
        activate_tree_filter_selection=activate_tree_filter_selection,
        open_tree_filter=open_tree_filter,
        apply_tree_filter_query=apply_tree_filter_query,
        jump_to_path=navigation_proxy.jump_to_path,
    )
    handle_tree_mouse_click = mouse_handlers.handle_tree_mouse_click
    tick_source_selection_drag = mouse_handlers.tick_source_selection_drag

    current_jump_location = navigation_ops.current_jump_location
    record_jump_if_changed = navigation_ops.record_jump_if_changed
    jump_to_next_git_modified = partial(
        _jump_to_next_git_modified,
        state=state,
        visible_content_rows=visible_content_rows,
        refresh_git_status_overlay=refresh_git_status_overlay,
        sorted_git_modified_file_paths=sorted_git_modified_file_paths,
        current_jump_location=current_jump_location,
        jump_to_path=navigation_ops.jump_to_path,
        record_jump_if_changed=record_jump_if_changed,
    )

    schedule_tree_filter_index_warmup()
    watch_refresh.tree_signature = build_tree_watch_signature(
        state.tree_root,
        state.expanded,
        state.show_hidden,
    )
    watch_refresh.tree_last_poll = time.monotonic()
    reset_git_watch_context()
    watch_refresh.git_signature = build_git_watch_signature(watch_refresh.git_dir)
    watch_refresh.git_last_poll = time.monotonic()
    refresh_git_status_overlay(force=True)

    launch_editor_for_path = partial(_launch_editor_for_path, terminal=terminal)
    launch_lazygit = partial(
        _launch_lazygit,
        state=state,
        terminal=terminal,
        show_inline_error=show_inline_error,
        sync_selected_target_after_tree_refresh=sync_selected_target_after_tree_refresh,
        mark_tree_watch_dirty=mark_tree_watch_dirty,
    )

    nav = navigation_ops

    normal_key_ops = NormalKeyOps(
        current_jump_location=current_jump_location,
        record_jump_if_changed=record_jump_if_changed,
        open_symbol_picker=nav.open_symbol_picker,
        reroot_to_parent=nav.reroot_to_parent,
        reroot_to_selected_target=nav.reroot_to_selected_target,
        toggle_hidden_files=nav.toggle_hidden_files,
        toggle_tree_pane=nav.toggle_tree_pane,
        toggle_wrap_mode=nav.toggle_wrap_mode,
        toggle_help_panel=nav.toggle_help_panel,
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
    handle_normal_key = partial(_dispatch_normal_key, state=state, ops=normal_key_ops)

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
        open_command_picker=nav.open_command_picker,
        close_picker=nav.close_picker,
        refresh_command_picker_matches=nav.refresh_command_picker_matches,
        activate_picker_selection=nav.activate_picker_selection,
        refresh_active_picker_matches=nav.refresh_active_picker_matches,
        handle_tree_mouse_wheel=handle_tree_mouse_wheel,
        handle_tree_mouse_click=handle_tree_mouse_click,
        toggle_help_panel=nav.toggle_help_panel,
        close_tree_filter=close_tree_filter,
        activate_tree_filter_selection=activate_tree_filter_selection,
        move_tree_selection=move_tree_selection,
        apply_tree_filter_query=apply_tree_filter_query,
        jump_to_next_content_hit=jump_to_next_content_hit,
        set_named_mark=nav.set_named_mark,
        jump_to_named_mark=nav.jump_to_named_mark,
        jump_back_in_history=nav.jump_back_in_history,
        jump_forward_in_history=nav.jump_forward_in_history,
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
