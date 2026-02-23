"""Runtime composition layer for lazyviewer.

Builds initial state, wires callbacks across runtime modules, and starts the loop.
This is the highest-level module where rendering, navigation, search, and git meet.
"""

from __future__ import annotations

import os
import subprocess
import shutil
import sys
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

from ..ansi import ANSI_ESCAPE_RE, build_screen_lines
from ..git_jumps import (
    GitModifiedJumpDeps,
)
from ..input import (
    TreeMouseCallbacks,
    TreeMouseHandlers,
    _handle_tree_mouse_wheel,
)
from ..source_pane import SourcePaneOps, copy_selected_source_range as copy_source_selection_range
from ..tree_sync import (
    PreviewSelectionDeps,
    TreeRefreshSyncDeps,
)
from ..index_warmup import TreeFilterIndexWarmupScheduler
from ..layout import PagerLayoutOps
from ..screen_utils import (
    _centered_scroll_start,
    _first_git_change_screen_line,
    _tree_order_key_for_relative_path,
)
from ..watch_refresh import (
    WatchRefreshContext,
    _refresh_git_status_overlay,
)
from ..config import (
    load_content_search_left_pane_percent,
    load_left_pane_percent,
    load_named_marks,
    save_content_search_left_pane_percent,
    save_left_pane_percent,
    load_show_hidden,
)
from ..editor import launch_editor
from ..git_status import collect_git_status_overlay
from ..highlight import colorize_source
from ..input import NormalKeyOps, handle_normal_key as handle_normal_key_event
from ..source_pane import (
    DIR_PREVIEW_GROWTH_STEP,
    DIR_PREVIEW_HARD_MAX_ENTRIES,
    DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    build_rendered_for_path,
    clear_directory_preview_cache,
)
from ..source_pane.diff import clear_diff_preview_cache
from ..source_pane.events import directory_preview_target_for_display_line as preview_directory_preview_target_for_display_line
from ..render import help_panel_row_count
from .loop import RuntimeLoopCallbacks, RuntimeLoopTiming, run_main_loop
from ..picker_panel import NavigationPickerDeps, NavigationPickerOps
from ..filter_panel import TreeFilterDeps, TreeFilterOps
from ..search.fuzzy import collect_project_file_labels
from ..state import AppState
from ..terminal import TerminalController
from ..tree import (
    build_tree_entries,
    clamp_left_width,
    compute_left_width,
)
from ..watch import build_git_watch_signature, build_tree_watch_signature, resolve_git_paths

DOUBLE_CLICK_SECONDS = 0.35
FILTER_CURSOR_BLINK_SECONDS = 0.5
TREE_FILTER_SPINNER_FRAME_SECONDS = 0.12
GIT_STATUS_REFRESH_SECONDS = 2.0
TREE_WATCH_POLL_SECONDS = 0.5
GIT_WATCH_POLL_SECONDS = 0.5
GIT_FEATURES_DEFAULT_ENABLED = True
TREE_SIZE_LABELS_DEFAULT_ENABLED = True
CONTENT_SEARCH_LEFT_PANE_MIN_PERCENT = 50.0
CONTENT_SEARCH_LEFT_PANE_FALLBACK_DELTA_PERCENT = 8.0
WRAP_STATUS_SECONDS = 1.2


def _skip_gitignored_for_hidden_mode(show_hidden: bool) -> bool:
    """Return whether gitignored paths should be excluded for current visibility mode."""
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


def _copy_text_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy across macOS, Windows, and common Linux tools."""
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


def _clear_status_message(state: AppState) -> None:
    """Clear transient status message and its expiration timestamp."""
    state.status_message = ""
    state.status_message_until = 0.0


def _set_status_message(state: AppState, message: str) -> None:
    """Set transient status message visible for a fixed short interval."""
    state.status_message = message
    state.status_message_until = time.monotonic() + WRAP_STATUS_SECONDS


def _clear_source_selection(state: AppState) -> bool:
    """Clear source text selection anchors, returning whether anything changed."""
    changed = state.source_selection_anchor is not None or state.source_selection_focus is not None
    state.source_selection_anchor = None
    state.source_selection_focus = None
    return changed


def _refresh_rendered_for_current_path(
    state: AppState,
    style: str,
    no_color: bool,
    rebuild_screen_lines: Callable[..., None],
    visible_content_rows: Callable[[], int],
    reset_scroll: bool = True,
    reset_dir_budget: bool = False,
    force_rebuild: bool = False,
) -> None:
    """Rebuild rendered preview text for ``state.current_path`` and sync derived fields."""
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
        dir_show_size_labels=state.show_tree_sizes,
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
    state: AppState,
    visible_content_rows: Callable[[], int],
    refresh_rendered_for_current_path: Callable[..., None],
) -> bool:
    """Expand directory preview budget when scrolling near truncated preview end."""
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
    state: AppState,
    refresh_git_status_overlay: Callable[..., None],
    refresh_rendered_for_current_path: Callable[..., None],
) -> None:
    """Toggle git-aware features and refresh overlays/rendering accordingly."""
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


def _toggle_tree_size_labels(
    state: AppState,
    refresh_rendered_for_current_path: Callable[..., None],
) -> None:
    """Toggle directory size labels in preview and refresh when relevant."""
    state.show_tree_sizes = not state.show_tree_sizes
    if state.current_path.resolve().is_dir():
        refresh_rendered_for_current_path(reset_scroll=False, reset_dir_budget=False)
    state.dirty = True


def _launch_lazygit(
    state: AppState,
    terminal: TerminalController,
    show_inline_error: Callable[[str], None],
    sync_selected_target_after_tree_refresh: Callable[..., None],
    mark_tree_watch_dirty: Callable[[], None],
) -> None:
    """Run ``lazygit`` in tree root and resync UI state after returning."""
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

class NavigationProxy:
    """Late-bound proxy exposing navigation operations before ops construction."""

    def __init__(self) -> None:
        """Initialize proxy with no bound navigation operations."""
        self._ops: NavigationPickerOps | None = None

    def bind(self, ops: NavigationPickerOps) -> None:
        """Attach concrete navigation operations implementation."""
        self._ops = ops

    def current_jump_location(self):
        """Delegate current jump-location lookup to bound navigation ops."""
        assert self._ops is not None
        return self._ops.current_jump_location()

    def record_jump_if_changed(self, origin: object) -> None:
        """Delegate conditional jump-history recording to bound navigation ops."""
        assert self._ops is not None
        self._ops.record_jump_if_changed(origin)

    def jump_to_path(self, target: Path) -> None:
        """Delegate path jump request to bound navigation ops."""
        assert self._ops is not None
        self._ops.jump_to_path(target)

    def jump_to_line(self, line_number: int) -> None:
        """Delegate line jump request to bound navigation ops."""
        assert self._ops is not None
        self._ops.jump_to_line(line_number)

def run_pager(content: str, path: Path, style: str, no_color: bool, nopager: bool) -> None:
    """Initialize pager runtime state, wire subsystems, and run event loop."""
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
        dir_show_size_labels=TREE_SIZE_LABELS_DEFAULT_ENABLED,
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
        show_tree_sizes=TREE_SIZE_LABELS_DEFAULT_ENABLED,
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
    index_warmup_scheduler = TreeFilterIndexWarmupScheduler(
        collect_project_file_labels=collect_project_file_labels,
        skip_gitignored_for_hidden_mode=_skip_gitignored_for_hidden_mode,
    )
    schedule_tree_filter_index_warmup = partial(index_warmup_scheduler.schedule_for_state, state)
    layout_ops = PagerLayoutOps(
        state,
        kitty_graphics_supported,
        help_panel_row_count=help_panel_row_count,
        build_screen_lines=build_screen_lines,
        get_terminal_size=shutil.get_terminal_size,
        load_content_search_left_pane_percent=load_content_search_left_pane_percent,
        load_left_pane_percent=load_left_pane_percent,
        save_content_search_left_pane_percent=save_content_search_left_pane_percent,
        save_left_pane_percent=save_left_pane_percent,
        compute_left_width=compute_left_width,
        clamp_left_width=clamp_left_width,
        content_search_left_pane_min_percent=CONTENT_SEARCH_LEFT_PANE_MIN_PERCENT,
        content_search_left_pane_fallback_delta_percent=CONTENT_SEARCH_LEFT_PANE_FALLBACK_DELTA_PERCENT,
    )
    visible_content_rows = layout_ops.visible_content_rows
    sync_left_width_for_tree_filter_mode = layout_ops.sync_left_width_for_tree_filter_mode
    save_left_pane_width_for_mode = layout_ops.save_left_pane_width_for_mode
    rebuild_screen_lines = layout_ops.rebuild_screen_lines
    show_inline_error = layout_ops.show_inline_error
    current_preview_image_path = layout_ops.current_preview_image_path
    current_preview_image_geometry = layout_ops.current_preview_image_geometry
    watch_refresh = WatchRefreshContext()
    mark_tree_watch_dirty = watch_refresh.mark_tree_dirty
    refresh_rendered_for_current_path = partial(
        _refresh_rendered_for_current_path,
        state,
        style,
        no_color,
        rebuild_screen_lines,
        visible_content_rows,
    )

    refresh_git_status_overlay = partial(
        _refresh_git_status_overlay,
        state,
        refresh_rendered_for_current_path,
        collect_git_status_overlay=collect_git_status_overlay,
        monotonic=time.monotonic,
        status_refresh_seconds=GIT_STATUS_REFRESH_SECONDS,
    )
    reset_git_watch_context = partial(
        watch_refresh.reset_git_context,
        state,
        resolve_git_paths=resolve_git_paths,
    )
    maybe_refresh_tree_watch: Callable[[], None]
    maybe_refresh_git_watch = partial(
        watch_refresh.maybe_refresh_git,
        state,
        refresh_git_status_overlay,
        refresh_rendered_for_current_path,
        build_git_watch_signature=build_git_watch_signature,
        monotonic=time.monotonic,
        git_watch_poll_seconds=GIT_WATCH_POLL_SECONDS,
    )

    clear_source_selection = partial(_clear_source_selection, state)
    toggle_git_features = partial(
        _toggle_git_features,
        state,
        refresh_git_status_overlay,
        refresh_rendered_for_current_path,
    )
    toggle_tree_size_labels = partial(
        _toggle_tree_size_labels,
        state,
        refresh_rendered_for_current_path,
    )
    preview_selected_entry: Callable[..., None]

    maybe_grow_directory_preview = partial(
        _maybe_grow_directory_preview,
        state,
        visible_content_rows,
        refresh_rendered_for_current_path,
    )

    source_pane_ops = SourcePaneOps(
        state,
        visible_content_rows,
        get_terminal_size=shutil.get_terminal_size,
    )
    max_horizontal_text_offset = source_pane_ops.max_horizontal_text_offset
    source_pane_col_bounds = source_pane_ops.source_pane_col_bounds
    source_selection_position = source_pane_ops.source_selection_position
    directory_preview_target_for_display_line = partial(preview_directory_preview_target_for_display_line, state)
    copy_selected_source_range = partial(
        copy_source_selection_range,
        state,
        copy_text_to_clipboard=_copy_text_to_clipboard,
    )
    handle_tree_mouse_wheel: Callable[[str], bool]
    handle_tree_mouse_click: Callable[[str], bool]
    tick_source_selection_drag: Callable[[], None]

    sync_selected_target_after_tree_refresh: Callable[..., None]
    navigation_proxy = NavigationProxy()
    preview_selection_deps = PreviewSelectionDeps(
        state=state,
        clear_source_selection=clear_source_selection,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        jump_to_line=navigation_proxy.jump_to_line,
    )

    preview_selected_entry = preview_selection_deps.preview_selected_entry

    tree_filter_deps = TreeFilterDeps(
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
    tree_filter_ops = TreeFilterOps(tree_filter_deps)

    coerce_tree_filter_result_index = tree_filter_ops.coerce_tree_filter_result_index
    move_tree_selection = tree_filter_ops.move_tree_selection
    rebuild_tree_entries = tree_filter_ops.rebuild_tree_entries
    apply_tree_filter_query = tree_filter_ops.apply_tree_filter_query
    open_tree_filter = tree_filter_ops.open_tree_filter
    close_tree_filter = tree_filter_ops.close_tree_filter
    activate_tree_filter_selection = tree_filter_ops.activate_tree_filter_selection
    jump_to_next_content_hit = tree_filter_ops.jump_to_next_content_hit
    tree_refresh_sync_deps = TreeRefreshSyncDeps(
        state=state,
        rebuild_tree_entries=rebuild_tree_entries,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        schedule_tree_filter_index_warmup=schedule_tree_filter_index_warmup,
        refresh_git_status_overlay=refresh_git_status_overlay,
    )
    sync_selected_target_after_tree_refresh = tree_refresh_sync_deps.sync_selected_target_after_tree_refresh
    maybe_refresh_tree_watch = partial(
        watch_refresh.maybe_refresh_tree,
        state,
        sync_selected_target_after_tree_refresh,
        build_tree_watch_signature=build_tree_watch_signature,
        monotonic=time.monotonic,
        tree_watch_poll_seconds=TREE_WATCH_POLL_SECONDS,
    )
    handle_tree_mouse_wheel = partial(
        _handle_tree_mouse_wheel,
        state,
        move_tree_selection,
        maybe_grow_directory_preview,
        max_horizontal_text_offset,
    )

    navigation_picker_deps = NavigationPickerDeps(
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
    navigation_ops = NavigationPickerOps(navigation_picker_deps)
    navigation_proxy.bind(navigation_ops)
    navigation_ops.set_open_tree_filter(open_tree_filter)
    tree_mouse_callbacks = TreeMouseCallbacks(
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
        copy_text_to_clipboard=_copy_text_to_clipboard,
        monotonic=time.monotonic,
    )
    mouse_handlers = TreeMouseHandlers(
        state,
        tree_mouse_callbacks,
        double_click_seconds=DOUBLE_CLICK_SECONDS,
    )
    handle_tree_mouse_click = mouse_handlers.handle_tree_mouse_click
    tick_source_selection_drag = mouse_handlers.tick_source_selection_drag

    current_jump_location = navigation_ops.current_jump_location
    record_jump_if_changed = navigation_ops.record_jump_if_changed
    git_modified_jump_deps = GitModifiedJumpDeps(
        state=state,
        visible_content_rows=visible_content_rows,
        refresh_git_status_overlay=refresh_git_status_overlay,
        current_jump_location=current_jump_location,
        jump_to_path=navigation_ops.jump_to_path,
        record_jump_if_changed=record_jump_if_changed,
        clear_status_message=partial(_clear_status_message, state),
        set_status_message=partial(_set_status_message, state),
    )
    jump_to_next_git_modified = git_modified_jump_deps.jump_to_next_git_modified

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

    launch_editor_for_path = lambda target: launch_editor(  # noqa: E731
        target,
        terminal.disable_tui_mode,
        terminal.enable_tui_mode,
    )
    launch_lazygit = partial(
        _launch_lazygit,
        state,
        terminal,
        show_inline_error,
        sync_selected_target_after_tree_refresh,
        mark_tree_watch_dirty,
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
        toggle_tree_size_labels=toggle_tree_size_labels,
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
    handle_normal_key = partial(
        handle_normal_key_event,
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
