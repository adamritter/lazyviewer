"""Runtime composition layer for lazyviewer.

Builds initial state, wires callbacks across runtime modules, and starts the loop.
This is the highest-level module where rendering, navigation, search, and git meet.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from functools import partial
from pathlib import Path

from ..render.ansi import build_screen_lines
from ..preview import (
    DIRECTORY_INITIAL_MAX_ENTRIES,
    DirectoryDocument,
    PreviewService,
)
from ..ports import PreviewSelectedEntry, SynchronizeTree
from .bootstrap import SessionBootstrap
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
from .coordinator import SessionCoordinator, SessionEffectHandlers
from .git_jumps import (
    GitModifiedJumpNavigator,
)
from ..source_pane import SourcePane
from ..source_pane.interaction.events import directory_preview_target_for_display_line
from ..source_pane.interaction.geometry import copy_selected_source_range
from ..source_pane.presenter import PreviewPresenter
from ..source_pane.preview_controller import PreviewController
from ..source_pane.syntax import colorize_source
from .application import App, ApplicationComponents, ApplicationOperations
from .preview_worker import PreviewLoaded, PreviewWorker
from ..session import PreviewCompleted, WorkspaceObserved
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
from ..render import help_panel_row_count
from ..search import SearchService
from .loop import RuntimeLoopTiming, run_main_loop
from ..tree_pane.pane import TreePane
from ..tree_pane.sync import PreviewSelection, TreeRefreshSync
from .terminal import TerminalController
from ..tree_model import (
    build_tree_entries,
    clamp_left_width,
    compute_left_width,
)
from ..ui_theme import normalize_theme_name, resolve_theme
from ..workspace import (
    WorkspaceDelta,
    WorkspaceIndexWarmup,
    WorkspaceQuery,
    WorkspaceService,
    WorkspaceWatcher,
)

DOUBLE_CLICK_SECONDS = 0.35
FILTER_CURSOR_BLINK_SECONDS = 0.5
TREE_FILTER_SPINNER_FRAME_SECONDS = 0.12
GIT_STATUS_REFRESH_SECONDS = 2.0
TREE_WATCH_POLL_SECONDS = 0.5
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
            rendered = colorize_source(content, path, style)
        sys.stdout.write(content if no_color else rendered)
        return

    selected_theme_name = normalize_theme_name(theme_name or load_theme_name())
    selected_theme = resolve_theme(selected_theme_name, no_color=no_color)
    workspace_service = WorkspaceService()
    preview_service = PreviewService()
    preview_presenter = PreviewPresenter(
        style=style,
        color=not no_color,
        theme=selected_theme,
    )
    session_bootstrap = SessionBootstrap(
        skip_gitignored_for_hidden_mode=_skip_gitignored_for_hidden_mode,
        load_show_hidden=load_show_hidden,
        load_named_marks=load_named_marks,
        load_left_pane_percent=load_left_pane_percent,
        compute_left_width=compute_left_width,
        clamp_left_width=clamp_left_width,
        build_tree_entries=build_tree_entries,
        workspace_service=workspace_service,
        preview_service=preview_service,
        preview_presenter=preview_presenter,
        git_features_default_enabled=GIT_FEATURES_DEFAULT_ENABLED,
        tree_size_labels_default_enabled=TREE_SIZE_LABELS_DEFAULT_ENABLED,
        dir_preview_initial_max_entries=DIRECTORY_INITIAL_MAX_ENTRIES,
        theme_name=selected_theme_name,
    )
    state = session_bootstrap.build_state(
        path=path,
        no_color=no_color,
        workspace_paths=workspace_paths,
    )
    search_service = SearchService(workspace_service)

    def workspace_query() -> WorkspaceQuery:
        roots = list(state.workspace.roots) or [state.workspace.active_root]
        expanded_by_root = list(state.workspace.expanded_by_root)
        return WorkspaceQuery.create(
            roots,
            expanded_by_root,
            show_hidden=state.workspace.show_hidden,
            skip_gitignored=_skip_gitignored_for_hidden_mode(state.workspace.show_hidden),
        )

    initial_workspace_snapshot = state.workspace.snapshot
    if initial_workspace_snapshot is None:  # Defensive: bootstrap always publishes one.
        initial_workspace_snapshot = workspace_service.snapshot(workspace_query())
        state.workspace.snapshot = initial_workspace_snapshot

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    terminal = TerminalController(stdin_fd, stdout_fd)
    kitty_graphics_supported = terminal.supports_kitty_graphics()
    index_warmup_scheduler = WorkspaceIndexWarmup(workspace_service.file_index)

    def current_workspace_snapshot():
        snapshot = state.workspace.snapshot
        query = workspace_query()
        if snapshot is None:
            snapshot = workspace_service.snapshot(query)
        elif snapshot.query != query:
            delta = workspace_service.refresh(snapshot, query)
            workspace_watcher.snapshot = delta.snapshot
            session_coordinator.dispatch(WorkspaceObserved(delta))
            snapshot = delta.snapshot
        return snapshot

    def schedule_tree_filter_index_warmup() -> None:
        index_warmup_scheduler.schedule(current_workspace_snapshot())
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
    workspace_watcher = WorkspaceWatcher(
        service=workspace_service,
        snapshot=initial_workspace_snapshot,
        tree_poll_seconds=TREE_WATCH_POLL_SECONDS,
        git_poll_seconds=GIT_STATUS_REFRESH_SECONDS,
    )
    mark_tree_watch_dirty = workspace_watcher.mark_tree_dirty
    preview_controller = PreviewController(
        state=state,
        service=preview_service,
        presenter=preview_presenter,
        rebuild_screen_lines=rebuild_screen_lines,
        visible_content_rows=visible_content_rows,
    )
    refresh_rendered_for_current_path = preview_controller.refresh

    sync_selected_target_after_tree_refresh: SynchronizeTree

    session_coordinator: SessionCoordinator

    def apply_workspace_delta(delta: WorkspaceDelta) -> None:
        session_coordinator.dispatch(WorkspaceObserved(delta))

    def refresh_git_status_overlay(force: bool = False) -> None:
        if not state.git.enabled:
            if state.git.status:
                state.git.status = {}
                state.interface.dirty = True
            state.git.last_refresh = time.monotonic()
            return
        now = time.monotonic()
        delta = workspace_watcher.poll_git(workspace_query(), now, force=force)
        if delta is None:
            return
        state.git.last_refresh = now
        apply_workspace_delta(delta)

    def reset_git_watch_context() -> None:
        workspace_watcher.mark_git_dirty()

    def maybe_refresh_tree_watch() -> None:
        delta = workspace_watcher.poll_tree(workspace_query(), time.monotonic())
        if delta is not None:
            apply_workspace_delta(delta)

    def maybe_refresh_git_watch() -> None:
        refresh_git_status_overlay()

    clear_source_selection = partial(_clear_source_selection, state)
    toggle_git_features = partial(
        _toggle_git_features,
        state,
        refresh_git_status_overlay,
        refresh_rendered_for_current_path,
    )
    toggle_tree_size_labels = preview_controller.toggle_size_labels
    preview_selected_entry: PreviewSelectedEntry

    maybe_grow_directory_preview = preview_controller.maybe_grow
    preview_worker = PreviewWorker(preview_service)
    prefetch_requested_context: tuple[Path, str, bool, bool, bool] | None = None
    prefetch_requested_entries = 0
    pending_async_preview_context: tuple[Path, str, bool, bool, bool] | None = None
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
            preview_controller.workspace_revision,
            state.workspace.show_hidden,
            state.workspace.show_sizes,
            state.git.enabled,
        )
        if context != prefetch_requested_context:
            prefetch_requested_context = context
            prefetch_requested_entries = state.preview.directory_max_entries
        preview_worker.schedule(
            preview_controller.request(resolved_target, max_entries=dir_max_entries)
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
        if reset_dir_budget or state.preview.directory_path != resolved_target:
            state.preview.directory_max_entries = PreviewController.initial_max_entries(
                visible_content_rows()
            )
        pending_async_preview_context = (
            resolved_target,
            preview_controller.workspace_revision,
            state.workspace.show_hidden,
            state.workspace.show_sizes,
            state.git.enabled,
        )
        pending_async_preview_reset_scroll = bool(reset_scroll)
        if reset_scroll:
            state.preview.scroll = 0
            state.preview.horizontal_scroll = 0
        schedule_directory_preview_request(
            resolved_target,
            state.preview.directory_max_entries,
        )

    def maybe_poll_directory_preview_results() -> bool:
        """Apply completed directory-preview background jobs for current context."""
        nonlocal prefetch_requested_context
        nonlocal prefetch_requested_entries
        nonlocal pending_async_preview_context
        nonlocal pending_async_preview_reset_scroll
        changed = False
        resolved_target = state.workspace.current_path.resolve()
        context = (
            resolved_target,
            preview_controller.workspace_revision,
            state.workspace.show_hidden,
            state.workspace.show_sizes,
            state.git.enabled,
        )
        if context != prefetch_requested_context:
            prefetch_requested_context = context
            prefetch_requested_entries = state.preview.directory_max_entries

        best_result: PreviewLoaded | None = None
        for result in preview_worker.drain():
            request = result.request
            if request.target != resolved_target:
                continue
            if request.workspace_revision != preview_controller.workspace_revision:
                continue
            if request.show_hidden != state.workspace.show_hidden:
                continue
            if request.skip_gitignored != (not state.workspace.show_hidden):
                continue
            if request.show_size_labels != state.workspace.show_sizes:
                continue
            if request.directory_max_entries < state.preview.directory_max_entries:
                continue
            if not isinstance(result.document, DirectoryDocument):
                continue
            if (
                best_result is None
                or request.directory_max_entries
                >= best_result.request.directory_max_entries
            ):
                best_result = result

        if best_result is not None:
            state.preview.directory_max_entries = best_result.request.directory_max_entries
            apply_reset_scroll = False
            if pending_async_preview_context == context:
                apply_reset_scroll = pending_async_preview_reset_scroll
                pending_async_preview_context = None
                pending_async_preview_reset_scroll = False
            effects = session_coordinator.dispatch(
                PreviewCompleted(
                    best_result.request,
                    best_result.document,
                    apply_reset_scroll,
                )
            )
            changed = bool(effects)
            prefetch_requested_entries = max(
                prefetch_requested_entries,
                best_result.request.directory_max_entries,
            )

        return changed

    def maybe_prefetch_directory_preview() -> bool:
        changed = maybe_poll_directory_preview_results()
        resolved_target = state.workspace.current_path.resolve()
        if not resolved_target.is_dir():
            return changed

        target_entries = preview_controller.prefetch_target_entries()
        if target_entries is None:
            return changed

        if target_entries <= max(state.preview.directory_max_entries, prefetch_requested_entries):
            return changed

        schedule_directory_preview_request(resolved_target, target_entries)
        return changed

    resolve_directory_preview_target = partial(directory_preview_target_for_display_line, state)
    copy_source_range = partial(
        copy_selected_source_range,
        state,
        copy_text_to_clipboard=_copy_text_to_clipboard,
    )
    source_pane_runtime: SourcePane
    tree_pane_runtime: TreePane

    preview_selection = PreviewSelection(
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
        search_service=search_service,
    )
    preview_selection.bind_jump_to_line(tree_pane_runtime.navigation.jump_to_line)
    source_pane_runtime = SourcePane(
        state=state,
        visible_content_rows=visible_content_rows,
        move_tree_selection=tree_pane_runtime.filter.move_tree_selection,
        maybe_grow_directory_preview=maybe_grow_directory_preview,
        clear_source_selection=clear_source_selection,
        copy_selected_source_range=copy_source_range,
        directory_preview_target_for_display_line=resolve_directory_preview_target,
        open_tree_filter=tree_pane_runtime.filter_panel.open,
        apply_tree_filter_query=tree_pane_runtime.filter.apply_tree_filter_query,
        jump_to_path=tree_pane_runtime.navigation.jump_to_path,
        get_terminal_size=shutil.get_terminal_size,
    )
    tree_refresh_sync = TreeRefreshSync(
        state=state,
        rebuild_tree_entries=tree_pane_runtime.filter.rebuild_tree_entries,
        refresh_rendered_for_current_path=refresh_rendered_for_current_path,
        schedule_tree_filter_index_warmup=schedule_tree_filter_index_warmup,
        refresh_git_status_overlay=refresh_git_status_overlay,
    )
    sync_selected_target_after_tree_refresh = tree_refresh_sync.sync_selected_target_after_tree_refresh
    session_coordinator = SessionCoordinator(
        state,
        SessionEffectHandlers(
            reflow_preview=lambda columns: rebuild_screen_lines(columns=columns),
            save_pane_width=save_left_pane_width_for_mode,
            synchronize_workspace=lambda preferred: sync_selected_target_after_tree_refresh(
                preferred_path=preferred
            ),
            refresh_preview=lambda reset_scroll, reset_budget: refresh_rendered_for_current_path(
                reset_scroll=reset_scroll,
                reset_dir_budget=reset_budget,
            ),
            apply_preview=lambda request, document, reset_scroll: preview_controller.apply(
                request,
                document,
                preview_presenter.present(document),
                reset_scroll=reset_scroll,
            ),
        ),
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
    workspace_watcher.tree_last_poll = time.monotonic()
    reset_git_watch_context()
    workspace_watcher.git_last_poll = time.monotonic()
    refresh_git_status_overlay(force=True)

    def launch_editor_for_path(target: Path) -> str | None:
        return launch_editor(
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
        components=ApplicationComponents(
            state=state,
            terminal=terminal,
            layout=layout,
            source_pane=source_pane_runtime,
            tree_pane=tree_pane_runtime,
        ),
        operations=ApplicationOperations(
            maybe_refresh_tree_watch=maybe_refresh_tree_watch,
            maybe_refresh_git_watch=maybe_refresh_git_watch,
            refresh_git_status=refresh_git_status_overlay,
            toggle_tree_size_labels=toggle_tree_size_labels,
            toggle_git_features=toggle_git_features,
            launch_lazygit=launch_lazygit,
            mark_tree_watch_dirty=mark_tree_watch_dirty,
            preview_selected_entry=preview_selected_entry,
            refresh_preview=refresh_rendered_for_current_path,
            maybe_grow_directory_preview=maybe_grow_directory_preview,
            poll_preview_results=maybe_poll_directory_preview_results,
            prefetch_directory_preview=maybe_prefetch_directory_preview,
            launch_editor_for_path=launch_editor_for_path,
            jump_to_next_git_modified=jump_to_next_git_modified,
            save_left_pane_width=save_left_pane_width_for_mode,
            run_main_loop=run_main_loop,
        ),
        stdin_fd=stdin_fd,
        timing=loop_timing,
    )
    app.run()
