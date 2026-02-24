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
from .app_bootstrap import AppStateBootstrapDeps, build_initial_app_state
from .app_helpers import (
    clear_source_selection as _clear_source_selection,
    clear_status_message as _clear_status_message,
    copy_text_to_clipboard as _copy_text_to_clipboard,
    launch_lazygit as _launch_lazygit,
    maybe_grow_directory_preview as _maybe_grow_directory_preview,
    refresh_rendered_for_current_path as _refresh_rendered_for_current_path,
    set_status_message as _set_status_message,
    skip_gitignored_for_hidden_mode as _skip_gitignored_for_hidden_mode,
    toggle_git_features as _toggle_git_features,
    toggle_tree_size_labels as _toggle_tree_size_labels,
)
from .command_palette import COMMAND_PALETTE_ITEMS
from .git_jumps import (
    GitModifiedJumpDeps,
)
from ..input import (
    TreeMouseCallbacks,
    TreeMouseHandlers,
    _handle_tree_mouse_wheel,
)
from ..source_pane import SourcePaneOps, copy_selected_source_range as copy_source_selection_range
from .tree_sync import (
    PreviewSelectionDeps,
    TreeRefreshSyncDeps,
)
from .index_warmup import TreeFilterIndexWarmupScheduler
from .layout import PagerLayoutOps
from .navigation_proxy import NavigationProxy
from .watch_refresh import (
    WatchRefreshContext,
    _refresh_git_status_overlay,
)
from .config import (
    load_content_search_left_pane_percent,
    load_left_pane_percent,
    load_named_marks,
    save_content_search_left_pane_percent,
    save_left_pane_percent,
    load_show_hidden,
)
from .editor import launch_editor
from ..git_status import collect_git_status_overlay
from ..source_pane.syntax import colorize_source
from ..input import NormalKeyOps, handle_normal_key as handle_normal_key_event
from ..source_pane import (
    DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    build_rendered_for_path,
)
from ..source_pane.interaction.events import directory_preview_target_for_display_line as preview_directory_preview_target_for_display_line
from ..render import help_panel_row_count
from .loop import RuntimeLoopCallbacks, RuntimeLoopTiming, run_main_loop
from ..tree_pane.panels.picker import NavigationPickerDeps, NavigationPickerOps
from ..tree_pane.panels.filter import TreeFilterDeps, TreeFilterOps
from ..search.fuzzy import collect_project_file_labels
from .terminal import TerminalController
from ..tree_pane.model import (
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

def run_pager(content: str, path: Path, style: str, no_color: bool, nopager: bool) -> None:
    """Initialize pager runtime state, wire subsystems, and run event loop."""
    if nopager or not os.isatty(sys.stdin.fileno()):
        rendered = content
        if not no_color and os.isatty(sys.stdout.fileno()):
            rendered = colorize_source(content, path, style)
        sys.stdout.write(content if no_color else rendered)
        return

    state_bootstrap_deps = AppStateBootstrapDeps(
        skip_gitignored_for_hidden_mode=_skip_gitignored_for_hidden_mode,
        load_show_hidden=load_show_hidden,
        load_named_marks=load_named_marks,
        load_left_pane_percent=load_left_pane_percent,
        compute_left_width=compute_left_width,
        clamp_left_width=clamp_left_width,
        build_tree_entries=build_tree_entries,
        build_rendered_for_path=build_rendered_for_path,
        git_features_default_enabled=GIT_FEATURES_DEFAULT_ENABLED,
        tree_size_labels_default_enabled=TREE_SIZE_LABELS_DEFAULT_ENABLED,
        dir_preview_initial_max_entries=DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    )
    state = build_initial_app_state(
        path=path,
        style=style,
        no_color=no_color,
        deps=state_bootstrap_deps,
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
