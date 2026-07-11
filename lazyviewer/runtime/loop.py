"""Main interactive event loop for the terminal UI.

Coordinates periodic refreshes, rendering, and all input dispatch.
This loop is intentionally wiring-heavy; feature logic lives in pane/panel objects.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ..input import (
    AdjustPaneWidth,
    DispatchKey,
    IgnoreInput,
    coalesce_mouse_wheel_events,
    has_pending_input,
    map_input,
    read_key,
)
from ..render import Frame, RenderContext, render_dual_page_context
from ..session import (
    ClockTicked,
    PaneWidthAdjusted,
    ReflowPreview,
    SavePaneWidth,
    SessionState,
    TerminalResized,
    update,
)
from .terminal import TerminalController

IDLE_DIRECTORY_PREFETCH_DELAY_SECONDS = 0.35
IDLE_DIRECTORY_PREFETCH_INTERVAL_SECONDS = 0.2


class RenderFrame(Protocol):
    """Retained or standalone frame-rendering port used by the loop."""

    def __call__(self, context: RenderContext) -> Frame: ...


@dataclass(frozen=True)
class RuntimeLoopTiming:
    """Timing constants controlling interactive loop behavior."""

    double_click_seconds: float
    filter_cursor_blink_seconds: float
    tree_filter_spinner_frame_seconds: float


@dataclass(frozen=True)
class RuntimeLoopCallbacks:
    """UI-root contract used by ``run_main_loop`` in tests and adapters."""

    tree_pane: object
    source_pane: object
    layout: object
    maybe_refresh_tree_watch: Callable[[], None]
    maybe_refresh_git_watch: Callable[[], None]
    refresh_git_status_overlay: Callable[[], None]
    handle_normal_key: Callable[[str, int], bool]
    save_left_pane_width: Callable[[int, int], None]
    handle_tree_mouse_wheel: Callable[[str], bool]
    handle_tree_mouse_click: Callable[[str], bool]
    handle_picker_key: Callable[[str, float], tuple[bool, bool]]
    handle_tree_filter_key: Callable[[str], bool]
    tick_source_selection_drag: Callable[[], None]
    tick_tree_filter_search: Callable[[float], bool]
    maybe_poll_directory_preview_results: Callable[[], bool]
    maybe_prefetch_directory_preview: Callable[[], bool]
    render_frame: RenderFrame | None = None


def run_main_loop(
    state: SessionState,
    terminal: TerminalController,
    stdin_fd: int,
    timing: RuntimeLoopTiming,
    callbacks: RuntimeLoopCallbacks,
) -> None:
    """Run the main interactive TUI loop until a quit action occurs.

    Each iteration handles terminal resize bookkeeping, optional rendering,
    input decoding/dispatch, and periodic idle refresh hooks.
    """
    tree_pane = callbacks.tree_pane
    source_pane = callbacks.source_pane
    layout = callbacks.layout

    get_tree_filter_loading_until = tree_pane.filter.get_loading_until
    tree_view_rows = tree_pane.filter.tree_view_rows
    tree_filter_prompt_prefix = tree_pane.filter.tree_filter_prompt_prefix
    tree_filter_placeholder = tree_pane.filter.tree_filter_placeholder
    visible_content_rows = source_pane.geometry.visible_content_rows
    rebuild_screen_lines = layout.rebuild_screen_lines
    current_preview_image_path = layout.current_preview_image_path
    current_preview_image_geometry = layout.current_preview_image_geometry
    open_command_picker = tree_pane.picker_panel.open_command_picker
    set_named_mark = tree_pane.navigation.set_named_mark
    jump_to_named_mark = tree_pane.navigation.jump_to_named_mark
    jump_back_in_history = tree_pane.navigation.jump_back_in_history
    jump_forward_in_history = tree_pane.navigation.jump_forward_in_history
    toggle_tree_filter_mode = tree_pane.filter_panel.toggle_mode
    tick_source_selection_drag = callbacks.tick_source_selection_drag
    tick_tree_filter_search = callbacks.tick_tree_filter_search
    picker_key_dispatch = callbacks.handle_picker_key
    tree_filter_key_dispatch = callbacks.handle_tree_filter_key

    maybe_refresh_tree_watch = callbacks.maybe_refresh_tree_watch
    maybe_refresh_git_watch = callbacks.maybe_refresh_git_watch
    refresh_git_status_overlay = callbacks.refresh_git_status_overlay
    handle_normal_key = callbacks.handle_normal_key
    save_left_pane_width = callbacks.save_left_pane_width
    maybe_poll_directory_preview_results = callbacks.maybe_poll_directory_preview_results
    maybe_prefetch_directory_preview = callbacks.maybe_prefetch_directory_preview
    render_frame = callbacks.render_frame
    kitty_image_state: tuple[str, int, int, int, int] | None = None
    tree_filter_cursor_visible = True
    tree_filter_spinner_frame = 0
    last_input_at = time.monotonic()
    last_idle_directory_prefetch_at = 0.0

    def adjust_left_pane_width(term_columns: int, delta: int) -> None:
        """Resize tree pane width, persist it, and reflow text if needed."""
        for effect in update(state, PaneWidthAdjusted(term_columns, delta)):
            if isinstance(effect, SavePaneWidth):
                save_left_pane_width(effect.columns, effect.left_width)
            elif isinstance(effect, ReflowPreview):
                rebuild_screen_lines(columns=effect.columns)

    with terminal.raw_mode():
        while True:
            tick_tree_filter_search(0.0)
            input_backlogged = has_pending_input(stdin_fd)
            if not input_backlogged and maybe_poll_directory_preview_results():
                state.interface.dirty = True
            term = shutil.get_terminal_size((80, 24))
            now = time.monotonic()
            terminal.set_mouse_reporting(True)
            update(state, ClockTicked(now))
            for effect in update(state, TerminalResized(term.columns, term.lines)):
                if isinstance(effect, ReflowPreview):
                    rebuild_screen_lines(columns=effect.columns)
            state.preview.max_scroll = max(0, len(state.preview.lines) - visible_content_rows())

            prev_tree_start = state.workspace.scroll
            visible_tree_rows = tree_view_rows()
            if state.workspace.selected < state.workspace.scroll:
                state.workspace.scroll = state.workspace.selected
            elif state.workspace.selected >= state.workspace.scroll + visible_tree_rows:
                state.workspace.scroll = state.workspace.selected - visible_tree_rows + 1
            state.workspace.scroll = max(
                0,
                min(state.workspace.scroll, max(0, len(state.workspace.entries) - visible_tree_rows)),
            )
            if state.workspace.scroll != prev_tree_start:
                state.interface.dirty = True

            if state.picker.active and state.picker.mode in {"symbols", "commands"}:
                picker_rows = max(1, visible_content_rows() - 1)
                max_picker_start = max(0, len(state.picker.match_labels) - picker_rows)
                prev_picker_start = state.picker.list_start
                if state.picker.selected < state.picker.list_start:
                    state.picker.list_start = state.picker.selected
                elif state.picker.selected >= state.picker.list_start + picker_rows:
                    state.picker.list_start = state.picker.selected - picker_rows + 1
                state.picker.list_start = max(0, min(state.picker.list_start, max_picker_start))
                if state.picker.list_start != prev_picker_start:
                    state.interface.dirty = True

            blinking_filter = (
                state.filter.active
                and state.filter.editing
                and not state.picker.active
                and state.layout.browser_visible
            )
            if blinking_filter:
                blink_phase = (int(time.monotonic() / timing.filter_cursor_blink_seconds) % 2) == 0
                if blink_phase != tree_filter_cursor_visible:
                    tree_filter_cursor_visible = blink_phase
                    state.interface.dirty = True
            elif not tree_filter_cursor_visible:
                tree_filter_cursor_visible = True
                state.interface.dirty = True

            loading_active = bool(
                state.filter.active
                and state.filter.query
                and not state.picker.active
                and time.monotonic() < get_tree_filter_loading_until()
            )
            if loading_active != state.filter.loading:
                state.filter.loading = loading_active
                state.interface.dirty = True
            if state.filter.loading:
                next_spinner_frame = int(time.monotonic() / timing.tree_filter_spinner_frame_seconds)
                if next_spinner_frame != tree_filter_spinner_frame:
                    tree_filter_spinner_frame = next_spinner_frame
                    state.interface.dirty = True

            if state.interface.dirty and not input_backlogged:
                preview_image_path = current_preview_image_path()
                render_lines = [""] if preview_image_path is not None else state.preview.lines
                render_start = 0 if preview_image_path is not None else state.preview.scroll
                render_context = RenderContext(
                    text_lines=render_lines,
                    text_start=render_start,
                    tree_entries=state.workspace.entries,
                    tree_start=state.workspace.scroll,
                    tree_selected=state.workspace.selected,
                    max_lines=state.layout.usable_rows,
                    current_path=state.workspace.current_path,
                    tree_root=state.workspace.active_root,
                    tree_roots=state.workspace.roots,
                    workspace_expanded=state.workspace.expanded_by_root,
                    expanded=state.workspace.render_expanded,
                    width=term.columns,
                    left_width=state.layout.left_width,
                    text_x=state.preview.horizontal_scroll,
                    wrap_text=state.preview.wrap,
                    browser_visible=state.layout.browser_visible,
                    show_hidden=state.workspace.show_hidden,
                    show_help=state.layout.show_help,
                    show_tree_sizes=state.workspace.show_sizes,
                    status_message=state.interface.status_message,
                    tree_filter_active=state.filter.active,
                    tree_filter_row_visible=state.filter.prompt_row_visible,
                    tree_filter_mode=state.filter.mode,
                    tree_filter_query=state.filter.query,
                    tree_filter_editing=state.filter.editing,
                    tree_filter_cursor_visible=tree_filter_cursor_visible,
                    tree_filter_match_count=state.filter.match_count,
                    tree_filter_truncated=state.filter.truncated,
                    tree_filter_loading=state.filter.loading,
                    tree_filter_spinner_frame=tree_filter_spinner_frame,
                    tree_filter_prefix=tree_filter_prompt_prefix(),
                    tree_filter_placeholder=tree_filter_placeholder(),
                    picker_active=state.picker.active,
                    picker_mode=state.picker.mode,
                    picker_query=state.picker.query,
                    picker_items=state.picker.match_labels,
                    picker_selected=state.picker.selected,
                    picker_focus=state.picker.focus,
                    picker_list_start=state.picker.list_start,
                    picker_message=state.picker.message,
                    git_status_overlay=state.git.status,
                    tree_search_query=(
                        state.filter.query
                        if state.filter.active and state.filter.mode == "content"
                        else ""
                    ),
                    text_search_query=(
                        state.filter.query
                        if state.filter.active and state.filter.mode == "content"
                        else ""
                    ),
                    text_search_current_line=(
                        state.preview.search_current_line
                        if state.filter.active
                        and state.filter.mode == "content"
                        and state.filter.query
                        else 0
                    ),
                    text_search_current_column=(
                        state.preview.search_current_column
                        if state.filter.active
                        and state.filter.mode == "content"
                        and state.filter.query
                        else 0
                    ),
                    preview_is_git_diff=state.preview.is_git_diff,
                    source_selection_anchor=state.preview.selection_anchor,
                    source_selection_focus=state.preview.selection_focus,
                    theme=state.layout.theme,
                )
                frame = (
                    render_dual_page_context(render_context)
                    if render_frame is None
                    else render_frame(render_context)
                )
                terminal.write_frame(frame)
                desired_image_state: tuple[str, int, int, int, int] | None = None
                if preview_image_path is not None:
                    image_col, image_row, image_width, image_height = current_preview_image_geometry(
                        term.columns
                    )
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
                state.interface.dirty = False

            try:
                key = read_key(stdin_fd, timeout_ms=120)
            except KeyboardInterrupt:
                # Ignore SIGINT-style interrupts so terminal copy shortcuts do not exit the app.
                continue
            if key == "":
                maybe_refresh_tree_watch()
                maybe_refresh_git_watch()
                refresh_git_status_overlay()
                tick_source_selection_drag()
                idle_now = time.monotonic()
                idle_duration = idle_now - last_input_at
                prefetch_elapsed = idle_now - last_idle_directory_prefetch_at
                if (
                    idle_duration >= IDLE_DIRECTORY_PREFETCH_DELAY_SECONDS
                    and prefetch_elapsed >= IDLE_DIRECTORY_PREFETCH_INTERVAL_SECONDS
                ):
                    last_idle_directory_prefetch_at = idle_now
                    if maybe_prefetch_directory_preview():
                        state.interface.dirty = True
                continue
            key = coalesce_mouse_wheel_events(stdin_fd, key)
            last_input_at = time.monotonic()
            command = map_input(key, state)
            if isinstance(command, IgnoreInput):
                continue
            if isinstance(command, AdjustPaneWidth):
                adjust_left_pane_width(term.columns, command.delta)
                continue
            assert isinstance(command, DispatchKey)
            key = command.key

            if state.navigation.pending_mark_set:
                state.navigation.pending_mark_set = False
                state.navigation.pending_mark_jump = False
                state.interface.count_buffer = ""
                if key == "ESC":
                    continue
                if set_named_mark(key):
                    state.interface.dirty = True
                continue

            if state.navigation.pending_mark_jump:
                state.navigation.pending_mark_set = False
                state.navigation.pending_mark_jump = False
                state.interface.count_buffer = ""
                if key == "ESC":
                    continue
                if jump_to_named_mark(key):
                    state.interface.dirty = True
                continue

            tree_filter_editing_active = state.filter.active and state.filter.editing
            nav_hotkeys_enabled = not state.picker.active and not tree_filter_editing_active

            if key in {"ALT_LEFT", "ALT_RIGHT"} and nav_hotkeys_enabled:
                state.interface.count_buffer = ""
                moved = jump_back_in_history() if key == "ALT_LEFT" else jump_forward_in_history()
                if moved:
                    state.interface.dirty = True
                continue

            if key in {"CTRL_P", "/"} and not state.picker.active:
                if not (key == "/" and tree_filter_editing_active):
                    state.interface.count_buffer = ""
                    toggle_tree_filter_mode("files" if key == "CTRL_P" else "content")
                    continue

            if key == ":" and not state.picker.active:
                state.interface.count_buffer = ""
                open_command_picker()
                continue

            picker_handled, picker_should_quit = picker_key_dispatch(key, timing.double_click_seconds)
            if picker_should_quit:
                break
            if picker_handled:
                continue
            tree_filter_handled = tree_filter_key_dispatch(key)
            if tree_filter_handled:
                continue
            if handle_normal_key(key, term.columns):
                break
