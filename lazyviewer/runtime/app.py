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

from ..render.ansi import ANSI_ESCAPE_RE, build_screen_lines
from .app_bootstrap import AppStateBootstrap
from .app_helpers import (
    clear_source_selection as _clear_source_selection,
    clear_status_message as _clear_status_message,
    copy_text_to_clipboard as _copy_text_to_clipboard,
    launch_lazygit as _launch_lazygit,
    set_status_message as _set_status_message,
    skip_gitignored_for_hidden_mode as _skip_gitignored_for_hidden_mode,
    toggle_git_features as _toggle_git_features,
)
from .command_palette import COMMAND_PALETTE_ITEMS
from .git_jumps import (
    GitModifiedJumpNavigator,
)
from ..source_pane import SourcePane
from .application import App
from .directory_prefetch import (
    DirectoryPreviewPrefetchResult,
    DirectoryPreviewPrefetchScheduler,
)
from .index_warmup import TreeFilterIndexWarmupScheduler
from .layout import PagerLayout
from .config import (
    load_content_search_left_pane_percent,
    load_left_pane_percent,
    load_named_marks,
    load_theme_name,
    save_content_search_left_pane_percent,
    save_left_pane_percent,
    load_show_hidden,
)
from .editor import launch_editor
from ..git_status import collect_git_status_overlay
from ..render import help_panel_row_count
from .loop import RuntimeLoopTiming, run_main_loop
from ..tree_pane.pane import TreePane
from ..search.fuzzy import collect_project_file_labels
from .terminal import TerminalController
from ..tree_model import (
    build_tree_entries,
    clamp_left_width,
    compute_left_width,
)
from ..file_tree_model.watch import build_git_watch_signature, build_tree_watch_signature, resolve_git_paths
from ..ui_theme import normalize_theme_name

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


def run_pager(
    content: str,
    path: Path,
    style: str,
    no_color: bool,
    nopager: bool,
    theme_name: str | None = None,
    workspace_paths: list[Path] | None = None,
) -> None:
    """Initialize pager runtime state, wire subsystems, and run event loop."""
    if nopager or not os.isatty(sys.stdin.fileno()):
        rendered = content
        if not no_color and os.isatty(sys.stdout.fileno()):
            rendered = SourcePane.colorize_source(content, path, style)
        sys.stdout.write(content if no_color else rendered)
        return

    selected_theme_name = normalize_theme_name(theme_name or load_theme_name())
    state_bootstrap = AppStateBootstrap(
        skip_gitignored_for_hidden_mode=_skip_gitignored_for_hidden_mode,
        load_show_hidden=load_show_hidden,
        load_named_marks=load_named_marks,
        load_left_pane_percent=load_left_pane_percent,
        compute_left_width=compute_left_width,
        clamp_left_width=clamp_left_width,
        build_tree_entries=build_tree_entries,
        build_rendered_for_path=SourcePane.build_rendered_for_path,
        git_features_default_enabled=GIT_FEATURES_DEFAULT_ENABLED,
        tree_size_labels_default_enabled=TREE_SIZE_LABELS_DEFAULT_ENABLED,
        dir_preview_initial_max_entries=SourcePane.DIR_PREVIEW_INITIAL_MAX_ENTRIES,
        theme_name=selected_theme_name,
    )
    state = state_bootstrap.build_state(
        path=path,
        style=style,
        no_color=no_color,
        workspace_paths=workspace_paths,
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
    layout = PagerLayout(
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
    visible_content_rows = layout.visible_content_rows
    sync_left_width_for_tree_filter_mode = layout.sync_left_width_for_tree_filter_mode
    save_left_pane_width_for_mode = layout.save_left_pane_width_for_mode
    rebuild_screen_lines = layout.rebuild_screen_lines
    show_inline_error = layout.show_inline_error
    watch_refresh = TreePane.WatchRefreshContext()
    mark_tree_watch_dirty = watch_refresh.mark_tree_dirty
    refresh_rendered_for_current_path = partial(
        SourcePane.refresh_rendered_for_current_path,
        state,
        style,
        no_color,
        rebuild_screen_lines,
        visible_content_rows,
    )

    refresh_git_status_overlay = partial(
        TreePane.refresh_git_status_overlay,
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
        SourcePane.toggle_tree_size_labels,
        state,
        refresh_rendered_for_current_path,
    )
    preview_selected_entry: Callable[..., None]

    maybe_grow_directory_preview = partial(
        SourcePane.maybe_grow_directory_preview,
        state,
        visible_content_rows,
        refresh_rendered_for_current_path,
    )
    directory_prefetch_scheduler = DirectoryPreviewPrefetchScheduler(
        build_rendered_for_path=SourcePane.build_rendered_for_path,
    )
    prefetch_requested_context: tuple[Path, bool, bool, bool] | None = None
    prefetch_requested_entries = 0
    pending_async_preview_context: tuple[Path, bool, bool, bool] | None = None
    pending_async_preview_reset_scroll = False

    def schedule_directory_preview_request(
        target: Path,
        dir_max_entries: int,
    ) -> None:
        """Schedule one background directory preview request for target/context."""
        nonlocal prefetch_requested_context
        nonlocal prefetch_requested_entries
        resolved_target = target.resolve()
        context = (
            resolved_target,
            state.show_hidden,
            state.show_tree_sizes,
            state.git_features_enabled,
        )
        if context != prefetch_requested_context:
            prefetch_requested_context = context
            prefetch_requested_entries = state.dir_preview_max_entries
        prefer_git_diff = state.git_features_enabled and not (
            state.tree_filter_active
            and state.tree_filter_mode == "content"
            and bool(state.tree_filter_query)
        )
        directory_prefetch_scheduler.schedule(
            target=resolved_target,
            show_hidden=state.show_hidden,
            style=style,
            no_color=no_color,
            dir_max_entries=dir_max_entries,
            dir_skip_gitignored=not state.show_hidden,
            prefer_git_diff=prefer_git_diff,
            dir_git_status_overlay=(state.git_status_overlay if state.git_features_enabled else None),
            dir_show_size_labels=state.show_tree_sizes,
        )
        prefetch_requested_entries = max(prefetch_requested_entries, dir_max_entries)

    def request_directory_preview_async(
        target: Path,
        *,
        reset_scroll: bool = True,
        reset_dir_budget: bool = False,
    ) -> None:
        """Queue directory preview in background without blocking UI input."""
        nonlocal pending_async_preview_context
        nonlocal pending_async_preview_reset_scroll
        resolved_target = target.resolve()
        if not resolved_target.is_dir():
            return
        if reset_dir_budget or state.dir_preview_path != resolved_target:
            state.dir_preview_max_entries = SourcePane.initial_directory_preview_max_entries(
                visible_content_rows()
            )
        pending_async_preview_context = (
            resolved_target,
            state.show_hidden,
            state.show_tree_sizes,
            state.git_features_enabled,
        )
        pending_async_preview_reset_scroll = bool(reset_scroll)
        if reset_scroll:
            state.start = 0
            state.text_x = 0
        schedule_directory_preview_request(
            resolved_target,
            state.dir_preview_max_entries,
        )

    def maybe_poll_directory_preview_results() -> bool:
        """Apply completed directory-preview background jobs for current context."""
        nonlocal prefetch_requested_context
        nonlocal prefetch_requested_entries
        nonlocal pending_async_preview_context
        nonlocal pending_async_preview_reset_scroll
        changed = False
        resolved_target = state.current_path.resolve()
        context = (
            resolved_target,
            state.show_hidden,
            state.show_tree_sizes,
            state.git_features_enabled,
        )
        if context != prefetch_requested_context:
            prefetch_requested_context = context
            prefetch_requested_entries = state.dir_preview_max_entries

        best_result: DirectoryPreviewPrefetchResult | None = None
        for result in directory_prefetch_scheduler.drain_results():
            request = result.request
            if request.target != resolved_target:
                continue
            if request.show_hidden != state.show_hidden:
                continue
            if request.dir_skip_gitignored != (not state.show_hidden):
                continue
            if request.dir_show_size_labels != state.show_tree_sizes:
                continue
            if request.dir_max_entries < state.dir_preview_max_entries:
                continue
            if not getattr(result.rendered_for_path, "is_directory", False):
                continue
            if best_result is None or request.dir_max_entries >= best_result.request.dir_max_entries:
                best_result = result

        if best_result is not None:
            state.dir_preview_max_entries = best_result.request.dir_max_entries
            apply_reset_scroll = False
            if pending_async_preview_context == context:
                apply_reset_scroll = pending_async_preview_reset_scroll
                pending_async_preview_context = None
                pending_async_preview_reset_scroll = False
            SourcePane.apply_rendered_for_path(
                state,
                best_result.rendered_for_path,
                rebuild_screen_lines,
                visible_content_rows,
                reset_scroll=apply_reset_scroll,
                resolved_target=resolved_target,
            )
            changed = True
            prefetch_requested_entries = max(prefetch_requested_entries, best_result.request.dir_max_entries)

        return changed

    def maybe_prefetch_directory_preview() -> bool:
        changed = maybe_poll_directory_preview_results()
        resolved_target = state.current_path.resolve()
        if not resolved_target.is_dir():
            return changed

        target_entries = SourcePane.directory_prefetch_target_entries(
            state,
            visible_content_rows,
        )
        if target_entries is None:
            return changed

        if target_entries <= max(state.dir_preview_max_entries, prefetch_requested_entries):
            return changed

        schedule_directory_preview_request(resolved_target, target_entries)
        return changed

    directory_preview_target_for_display_line = partial(SourcePane.directory_preview_target_for_display_line, state)
    copy_selected_source_range = partial(
        SourcePane.copy_selected_source_range,
        state,
        copy_text_to_clipboard=_copy_text_to_clipboard,
    )
    source_pane_runtime: SourcePane
    tree_pane_runtime: TreePane

    sync_selected_target_after_tree_refresh: Callable[..., None]
    preview_selection = TreePane.PreviewSelection(
        state=state,
        clear_source_selection=clear_source_selection,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        request_directory_preview_async=request_directory_preview_async,
    )

    preview_selected_entry = preview_selection.preview_selected_entry

    tree_pane_runtime = TreePane(
        state=state,
        command_palette_items=COMMAND_PALETTE_ITEMS,
        visible_content_rows=visible_content_rows,
        rebuild_screen_lines=rebuild_screen_lines,
        preview_selected_entry=preview_selected_entry,
        schedule_tree_filter_index_warmup=schedule_tree_filter_index_warmup,
        mark_tree_watch_dirty=mark_tree_watch_dirty,
        reset_git_watch_context=reset_git_watch_context,
        refresh_git_status_overlay=refresh_git_status_overlay,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        copy_text_to_clipboard=_copy_text_to_clipboard,
        double_click_seconds=DOUBLE_CLICK_SECONDS,
        monotonic=time.monotonic,
        on_tree_filter_state_change=sync_left_width_for_tree_filter_mode,
    )
    preview_selection.bind_jump_to_line(tree_pane_runtime.navigation.jump_to_line)
    source_pane_runtime = SourcePane(
        state=state,
        visible_content_rows=visible_content_rows,
        move_tree_selection=tree_pane_runtime.filter.move_tree_selection,
        maybe_grow_directory_preview=maybe_grow_directory_preview,
        clear_source_selection=clear_source_selection,
        copy_selected_source_range=copy_selected_source_range,
        directory_preview_target_for_display_line=directory_preview_target_for_display_line,
        open_tree_filter=tree_pane_runtime.filter_panel.open,
        apply_tree_filter_query=tree_pane_runtime.filter.apply_tree_filter_query,
        jump_to_path=tree_pane_runtime.navigation.jump_to_path,
        get_terminal_size=shutil.get_terminal_size,
    )
    tree_refresh_sync = TreePane.TreeRefreshSync(
        state=state,
        rebuild_tree_entries=tree_pane_runtime.filter.rebuild_tree_entries,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        schedule_tree_filter_index_warmup=schedule_tree_filter_index_warmup,
        refresh_git_status_overlay=refresh_git_status_overlay,
    )
    sync_selected_target_after_tree_refresh = tree_refresh_sync.sync_selected_target_after_tree_refresh
    maybe_refresh_tree_watch = partial(
        watch_refresh.maybe_refresh_tree,
        state,
        sync_selected_target_after_tree_refresh,
        build_tree_watch_signature=build_tree_watch_signature,
        monotonic=time.monotonic,
        tree_watch_poll_seconds=TREE_WATCH_POLL_SECONDS,
    )

    current_jump_location = tree_pane_runtime.navigation.current_jump_location
    record_jump_if_changed = tree_pane_runtime.navigation.record_jump_if_changed
    git_modified_jump_navigator = GitModifiedJumpNavigator(
        state=state,
        visible_content_rows=visible_content_rows,
        refresh_git_status_overlay=refresh_git_status_overlay,
        current_jump_location=current_jump_location,
        jump_to_path=tree_pane_runtime.navigation.jump_to_path,
        record_jump_if_changed=record_jump_if_changed,
        clear_status_message=partial(_clear_status_message, state),
        set_status_message=partial(_set_status_message, state),
    )
    jump_to_next_git_modified = git_modified_jump_navigator.jump_to_next_git_modified

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

    loop_timing = RuntimeLoopTiming(
        double_click_seconds=DOUBLE_CLICK_SECONDS,
        filter_cursor_blink_seconds=FILTER_CURSOR_BLINK_SECONDS,
        tree_filter_spinner_frame_seconds=TREE_FILTER_SPINNER_FRAME_SECONDS,
    )
    app = App(
        state=state,
        terminal=terminal,
        stdin_fd=stdin_fd,
        timing=loop_timing,
        layout=layout,
        source_pane=source_pane_runtime,
        tree_pane=tree_pane_runtime,
        maybe_refresh_tree_watch=maybe_refresh_tree_watch,
        maybe_refresh_git_watch=maybe_refresh_git_watch,
        refresh_git_status_overlay=refresh_git_status_overlay,
        toggle_tree_size_labels=toggle_tree_size_labels,
        toggle_git_features=toggle_git_features,
        launch_lazygit=launch_lazygit,
        mark_tree_watch_dirty=mark_tree_watch_dirty,
        preview_selected_entry=preview_selected_entry,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        maybe_grow_directory_preview=maybe_grow_directory_preview,
        maybe_poll_directory_preview_results=maybe_poll_directory_preview_results,
        maybe_prefetch_directory_preview=maybe_prefetch_directory_preview,
        launch_editor_for_path=launch_editor_for_path,
        jump_to_next_git_modified=jump_to_next_git_modified,
        save_left_pane_width=save_left_pane_width_for_mode,
        run_main_loop_fn=run_main_loop,
    )
    app.run()
