"""Main interactive event loop for the terminal UI.

Coordinates periodic refreshes, rendering, and all input dispatch.
This loop is intentionally wiring-heavy; feature logic lives in pane/panel objects.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..input import read_key
from ..render import RenderContext, render_dual_page_context
from .state import AppState
from .terminal import TerminalController
from ..tree_model import clamp_left_width


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
    refresh_git_status_overlay: Callable[..., None]
    handle_normal_key: Callable[[str, int], bool]
    save_left_pane_width: Callable[[int, int], None]
    handle_tree_mouse_wheel: Callable[[str], bool] | None = None
    handle_tree_mouse_click: Callable[[str], bool] | None = None
    handle_picker_key: Callable[[str, float], tuple[bool, bool]] | None = None
    handle_tree_filter_key: Callable[[str], bool] | None = None
    tick_source_selection_drag: Callable[[], None] | None = None


def run_main_loop(
    state: AppState,
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
    handle_tree_mouse_wheel_override = getattr(callbacks, "handle_tree_mouse_wheel", None)
    handle_tree_mouse_wheel = (
        handle_tree_mouse_wheel_override
        if handle_tree_mouse_wheel_override is not None
        else source_pane.handle_tree_mouse_wheel
    )
    handle_tree_mouse_click_override = getattr(callbacks, "handle_tree_mouse_click", None)
    handle_tree_mouse_click = (
        handle_tree_mouse_click_override
        if handle_tree_mouse_click_override is not None
        else tree_pane.handle_tree_mouse_click
    )
    tick_source_selection_drag_override = getattr(callbacks, "tick_source_selection_drag", None)
    tick_source_selection_drag = (
        tick_source_selection_drag_override
        if tick_source_selection_drag_override is not None
        else source_pane.tick_source_selection_drag
    )
    handle_picker_key_override = getattr(callbacks, "handle_picker_key", None)
    if handle_picker_key_override is not None:
        picker_key_dispatch = handle_picker_key_override
    else:
        picker_key_dispatch = tree_pane.picker_panel.handle_key
    handle_tree_filter_key_override = getattr(callbacks, "handle_tree_filter_key", None)
    if handle_tree_filter_key_override is not None:
        tree_filter_key_dispatch = handle_tree_filter_key_override
    else:
        def tree_filter_key_dispatch(key: str) -> bool:
            return tree_pane.filter_panel.handle_key(
                key,
                handle_tree_mouse_wheel=handle_tree_mouse_wheel,
                handle_tree_mouse_click=handle_tree_mouse_click,
                toggle_help_panel=tree_pane.navigation.toggle_help_panel,
            )

    maybe_refresh_tree_watch = callbacks.maybe_refresh_tree_watch
    maybe_refresh_git_watch = callbacks.maybe_refresh_git_watch
    refresh_git_status_overlay = callbacks.refresh_git_status_overlay
    handle_normal_key = callbacks.handle_normal_key
    save_left_pane_width = callbacks.save_left_pane_width
    kitty_image_state: tuple[str, int, int, int, int] | None = None
    tree_filter_cursor_visible = True
    tree_filter_spinner_frame = 0

    def adjust_left_pane_width(term_columns: int, delta: int) -> None:
        """Resize tree pane width, persist it, and reflow text if needed."""
        prev_left = state.left_width
        state.left_width = clamp_left_width(term_columns, state.left_width + delta)
        if state.left_width == prev_left:
            return
        save_left_pane_width(term_columns, state.left_width)
        state.right_width = max(1, term_columns - state.left_width - 2)
        if state.right_width != state.last_right_width:
            state.last_right_width = state.right_width
            rebuild_screen_lines(columns=term_columns)
        state.dirty = True

    with terminal.raw_mode():
        while True:
            term = shutil.get_terminal_size((80, 24))
            now = time.monotonic()
            terminal.set_mouse_reporting(True)
            if state.status_message and now >= state.status_message_until:
                state.status_message = ""
                state.status_message_until = 0.0
                state.dirty = True
            previous_usable = state.usable
            previous_left_width = state.left_width
            state.usable = max(1, term.lines - 1)
            state.left_width = clamp_left_width(term.columns, state.left_width)
            if state.usable != previous_usable:
                state.dirty = True
            if state.left_width != previous_left_width:
                state.dirty = True
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
                blink_phase = (int(time.monotonic() / timing.filter_cursor_blink_seconds) % 2) == 0
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
                and time.monotonic() < get_tree_filter_loading_until()
            )
            if loading_active != state.tree_filter_loading:
                state.tree_filter_loading = loading_active
                state.dirty = True
            if state.tree_filter_loading:
                next_spinner_frame = int(time.monotonic() / timing.tree_filter_spinner_frame_seconds)
                if next_spinner_frame != tree_filter_spinner_frame:
                    tree_filter_spinner_frame = next_spinner_frame
                    state.dirty = True

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
                    show_tree_sizes=state.show_tree_sizes,
                    status_message=state.status_message,
                    tree_filter_active=state.tree_filter_active,
                    tree_filter_mode=state.tree_filter_mode,
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
                    preview_is_git_diff=state.preview_is_git_diff,
                    source_selection_anchor=state.source_selection_anchor,
                    source_selection_focus=state.source_selection_focus,
                )
                render_dual_page_context(render_context)
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
                state.dirty = False

            try:
                key = read_key(stdin_fd, timeout_ms=120)
            except KeyboardInterrupt:
                # Ignore SIGINT-style interrupts so terminal copy shortcuts do not exit the app.
                continue
            if key == "":
                maybe_refresh_tree_watch()
                maybe_refresh_git_watch()
                refresh_git_status_overlay()
                if tick_source_selection_drag is not None:
                    tick_source_selection_drag()
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
                adjust_left_pane_width(term.columns, -2)
                continue
            if key == "SHIFT_RIGHT":
                adjust_left_pane_width(term.columns, 2)
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

            tree_filter_editing_active = state.tree_filter_active and state.tree_filter_editing
            nav_hotkeys_enabled = not state.picker_active and not tree_filter_editing_active

            if key in {"ALT_LEFT", "ALT_RIGHT"} and nav_hotkeys_enabled:
                state.count_buffer = ""
                moved = jump_back_in_history() if key == "ALT_LEFT" else jump_forward_in_history()
                if moved:
                    state.dirty = True
                continue

            if key in {"CTRL_P", "/"} and not state.picker_active:
                if not (key == "/" and tree_filter_editing_active):
                    state.count_buffer = ""
                    toggle_tree_filter_mode("files" if key == "CTRL_P" else "content")
                    continue

            if key == ":" and not state.picker_active:
                state.count_buffer = ""
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
