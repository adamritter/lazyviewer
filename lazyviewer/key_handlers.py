"""Key-event handlers for picker, filter, and normal modes.

These functions convert normalized key tokens into state transitions.
They are kept mostly side-effect-free via injected callback operations.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .navigation import JumpLocation
from .state import AppState
from .tree import (
    next_directory_entry_index,
    next_index_after_directory_subtree,
    next_opened_directory_entry_index,
)


def _effective_max_start(state: AppState, visible_rows: int) -> int:
    return max(0, len(state.lines) - max(1, visible_rows))


def _parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
    parts = mouse_key.split(":")
    if len(parts) < 3:
        return None, None
    try:
        return int(parts[1]), int(parts[2])
    except Exception:
        return None, None


def _move_picker_selection(state: AppState, direction: int) -> None:
    if not state.picker_match_labels:
        return
    prev_selected = state.picker_selected
    state.picker_selected = max(
        0,
        min(len(state.picker_match_labels) - 1, state.picker_selected + direction),
    )
    if state.picker_selected != prev_selected:
        state.dirty = True


def _handle_picker_mouse_wheel(state: AppState, key: str) -> None:
    direction = -1 if key.startswith("MOUSE_WHEEL_UP:") else 1
    col, _row = _parse_mouse_col_row(key)
    if state.browser_visible and col is not None and col <= state.left_width:
        _move_picker_selection(state, direction)
        return
    prev_start = state.start
    state.start += direction * 3
    state.start = max(0, min(state.start, state.max_start))
    if state.start != prev_start:
        state.dirty = True


def _is_picker_tree_click(
    state: AppState,
    *,
    col: int | None,
    row: int | None,
    visible_rows: int,
) -> bool:
    return (
        state.browser_visible
        and col is not None
        and row is not None
        and 1 <= row <= visible_rows
        and col <= state.left_width
    )


def _handle_picker_list_click(
    state: AppState,
    *,
    row: int,
    double_click_seconds: float,
    activate_picker_selection: Callable[[], bool],
) -> bool:
    clicked_idx = state.picker_list_start + (row - 2)
    if not (0 <= clicked_idx < len(state.picker_match_labels)):
        return False
    prev_selected = state.picker_selected
    state.picker_selected = clicked_idx
    if state.picker_selected != prev_selected:
        state.dirty = True
    now = time.monotonic()
    is_double = clicked_idx == state.last_click_idx and (now - state.last_click_time) <= double_click_seconds
    state.last_click_idx = clicked_idx
    state.last_click_time = now
    if not is_double:
        return False
    should_quit = activate_picker_selection()
    if should_quit:
        return True
    state.dirty = True
    return False


def handle_picker_key(
    *,
    key: str,
    state: AppState,
    double_click_seconds: float,
    close_picker: Callable[[], None],
    refresh_command_picker_matches: Callable[..., None],
    activate_picker_selection: Callable[[], bool],
    visible_content_rows: Callable[[], int],
    refresh_active_picker_matches: Callable[..., None],
) -> tuple[bool, bool]:
    key_lower = key.lower()

    if not state.picker_active:
        return False, False

    if key == "ESC" or key == "\x03":
        close_picker()
        return True, False

    if state.picker_mode == "commands":
        if key == "UP" or key_lower == "k":
            _move_picker_selection(state, -1)
            return True, False
        if key == "DOWN" or key_lower == "j":
            _move_picker_selection(state, 1)
            return True, False
        if key == "BACKSPACE":
            if state.picker_query:
                state.picker_query = state.picker_query[:-1]
                refresh_command_picker_matches(reset_selection=True)
                state.dirty = True
            return True, False
        if len(key) == 1 and key.isprintable():
            state.picker_query += key
            refresh_command_picker_matches(reset_selection=True)
            state.dirty = True
            return True, False
        if key == "ENTER" or key_lower == "l":
            should_quit = activate_picker_selection()
            if should_quit:
                return True, True
            state.dirty = True
            return True, False
        if key == "TAB":
            return True, False
        if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
            _handle_picker_mouse_wheel(state, key)
            return True, False
        if key.startswith("MOUSE_LEFT_DOWN:"):
            col, row = _parse_mouse_col_row(key)
            if not _is_picker_tree_click(state, col=col, row=row, visible_rows=visible_content_rows()):
                return True, False
            if row is not None and row > 1:
                should_quit = _handle_picker_list_click(
                    state,
                    row=row,
                    double_click_seconds=double_click_seconds,
                    activate_picker_selection=activate_picker_selection,
                )
                if should_quit:
                    return True, True
            return True, False
        return True, False

    if key == "TAB":
        state.picker_focus = "tree" if state.picker_focus == "query" else "query"
        state.dirty = True
        return True, False

    if state.picker_focus == "query":
        if key == "ENTER":
            state.picker_focus = "tree"
            state.dirty = True
            return True, False
        if key == "BACKSPACE":
            if state.picker_query:
                state.picker_query = state.picker_query[:-1]
                refresh_active_picker_matches(reset_selection=True)
                state.dirty = True
            return True, False
        if len(key) == 1 and key.isprintable():
            state.picker_query += key
            refresh_active_picker_matches(reset_selection=True)
            state.dirty = True
        return True, False

    if key == "ENTER" or key_lower == "l":
        should_quit = activate_picker_selection()
        if should_quit:
            return True, True
        state.dirty = True
        return True, False
    if key == "UP" or key_lower == "k":
        _move_picker_selection(state, -1)
        return True, False
    if key == "DOWN" or key_lower == "j":
        _move_picker_selection(state, 1)
        return True, False
    if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
        _handle_picker_mouse_wheel(state, key)
        return True, False
    if key.startswith("MOUSE_LEFT_DOWN:"):
        col, row = _parse_mouse_col_row(key)
        if not _is_picker_tree_click(state, col=col, row=row, visible_rows=visible_content_rows()):
            return True, False
        if row == 1:
            state.picker_focus = "query"
            state.dirty = True
            return True, False
        if row is not None:
            should_quit = _handle_picker_list_click(
                state,
                row=row,
                double_click_seconds=double_click_seconds,
                activate_picker_selection=activate_picker_selection,
            )
            if should_quit:
                return True, True
        return True, False
    return True, False


def handle_tree_filter_key(
    *,
    key: str,
    state: AppState,
    handle_tree_mouse_wheel: Callable[[str], bool],
    handle_tree_mouse_click: Callable[[str], bool],
    toggle_help_panel: Callable[[], None],
    close_tree_filter: Callable[..., None],
    activate_tree_filter_selection: Callable[[], None],
    move_tree_selection: Callable[[int], bool],
    apply_tree_filter_query: Callable[..., None],
    jump_to_next_content_hit: Callable[[int], bool],
) -> bool:
    if state.tree_filter_active and state.tree_filter_editing:
        if handle_tree_mouse_wheel(key):
            return True
        if handle_tree_mouse_click(key):
            return True
        if key == "ESC":
            close_tree_filter(clear_query=True)
            return True
        if key == "ENTER":
            activate_tree_filter_selection()
            return True
        if key == "TAB":
            state.tree_filter_editing = False
            state.dirty = True
            return True
        if key == "UP" or key == "CTRL_K":
            if move_tree_selection(-1):
                state.dirty = True
            return True
        if key == "DOWN" or key == "CTRL_J":
            if move_tree_selection(1):
                state.dirty = True
            return True
        if key == "BACKSPACE":
            if state.tree_filter_query:
                apply_tree_filter_query(
                    state.tree_filter_query[:-1],
                    preview_selection=True,
                    select_first_file=True,
                )
            return True
        if key == "CTRL_U":
            if state.tree_filter_query:
                apply_tree_filter_query(
                    "",
                    preview_selection=True,
                    select_first_file=True,
                )
            return True
        if key == "CTRL_QUESTION":
            toggle_help_panel()
            return True
        if len(key) == 1 and key.isprintable():
            apply_tree_filter_query(
                state.tree_filter_query + key,
                preview_selection=True,
                select_first_file=True,
            )
            return True
        return True

    if state.tree_filter_active and not state.tree_filter_editing:
        if key == "TAB":
            state.tree_filter_editing = True
            state.dirty = True
            return True
        if key == "ENTER":
            activate_tree_filter_selection()
            return True
        if key == "ESC":
            close_tree_filter(clear_query=True)
            return True
        if state.tree_filter_mode == "content":
            if key == "n":
                if jump_to_next_content_hit(1):
                    state.dirty = True
                return True
            if key == "N":
                if jump_to_next_content_hit(-1):
                    state.dirty = True
                return True
    return False


@dataclass(frozen=True)
class NormalKeyOps:
    current_jump_location: Callable[[], JumpLocation]
    record_jump_if_changed: Callable[[JumpLocation], None]
    open_symbol_picker: Callable[[], None]
    reroot_to_parent: Callable[[], None]
    reroot_to_selected_target: Callable[[], None]
    toggle_hidden_files: Callable[[], None]
    toggle_tree_pane: Callable[[], None]
    toggle_wrap_mode: Callable[[], None]
    toggle_help_panel: Callable[[], None]
    toggle_git_features: Callable[[], None]
    launch_lazygit: Callable[[], None]
    handle_tree_mouse_wheel: Callable[[str], bool]
    handle_tree_mouse_click: Callable[[str], bool]
    move_tree_selection: Callable[[int], bool]
    rebuild_tree_entries: Callable[..., None]
    preview_selected_entry: Callable[..., None]
    refresh_rendered_for_current_path: Callable[..., None]
    refresh_git_status_overlay: Callable[..., None]
    maybe_grow_directory_preview: Callable[[], bool]
    visible_content_rows: Callable[[], int]
    rebuild_screen_lines: Callable[..., None]
    mark_tree_watch_dirty: Callable[[], None]
    launch_editor_for_path: Callable[[Path], str | None]
    jump_to_next_git_modified: Callable[[int], bool]


def handle_normal_key(
    *,
    key: str,
    term_columns: int,
    state: AppState,
    ops: NormalKeyOps,
) -> bool:
    current_jump_location = ops.current_jump_location
    record_jump_if_changed = ops.record_jump_if_changed
    open_symbol_picker = ops.open_symbol_picker
    reroot_to_parent = ops.reroot_to_parent
    reroot_to_selected_target = ops.reroot_to_selected_target
    toggle_hidden_files = ops.toggle_hidden_files
    toggle_tree_pane = ops.toggle_tree_pane
    toggle_wrap_mode = ops.toggle_wrap_mode
    toggle_help_panel = ops.toggle_help_panel
    toggle_git_features = ops.toggle_git_features
    launch_lazygit = ops.launch_lazygit
    handle_tree_mouse_wheel = ops.handle_tree_mouse_wheel
    handle_tree_mouse_click = ops.handle_tree_mouse_click
    move_tree_selection = ops.move_tree_selection
    rebuild_tree_entries = ops.rebuild_tree_entries
    preview_selected_entry = ops.preview_selected_entry
    refresh_rendered_for_current_path = ops.refresh_rendered_for_current_path
    refresh_git_status_overlay = ops.refresh_git_status_overlay
    maybe_grow_directory_preview = ops.maybe_grow_directory_preview
    visible_content_rows = ops.visible_content_rows
    rebuild_screen_lines = ops.rebuild_screen_lines
    mark_tree_watch_dirty = ops.mark_tree_watch_dirty
    launch_editor_for_path = ops.launch_editor_for_path
    jump_to_next_git_modified = ops.jump_to_next_git_modified

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
    if key in {"?", "CTRL_QUESTION"}:
        toggle_help_panel()
        return False
    if key == "CTRL_G":
        launch_lazygit()
        return False
    if key == "CTRL_O":
        toggle_git_features()
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
            edit_target = selected_entry.path.resolve()
        if edit_target is None:
            edit_target = state.current_path.resolve()

        error = launch_editor_for_path(edit_target)
        state.current_path = edit_target
        if error is None:
            if edit_target.is_dir():
                rebuild_tree_entries(preferred_path=edit_target)
                mark_tree_watch_dirty()
            refresh_rendered_for_current_path(
                reset_scroll=True,
                reset_dir_budget=True,
                force_rebuild=True,
            )
            refresh_git_status_overlay(force=True)
        else:
            state.rendered = f"\033[31m{error}\033[0m"
            rebuild_screen_lines(columns=term_columns, preserve_scroll=False)
            state.text_x = 0
            state.dir_preview_path = None
            state.dir_preview_truncated = False
            state.preview_image_path = None
            state.preview_image_format = None
            state.preview_is_git_diff = False
        state.dirty = True
        return False
    if key.lower() == "q" or key == "\x03":
        return True
    if not state.tree_filter_active and state.git_features_enabled and key == "n":
        if jump_to_next_git_modified(1):
            state.dirty = True
        return False
    if not state.tree_filter_active and state.git_features_enabled and key == "N":
        if jump_to_next_git_modified(-1):
            state.dirty = True
        return False
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
                if state.tree_filter_active and state.tree_filter_mode == "content":
                    state.tree_filter_collapsed_dirs.discard(resolved)
                state.expanded.add(resolved)
                rebuild_tree_entries(preferred_path=resolved)
                mark_tree_watch_dirty()
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
            if state.tree_filter_active and state.tree_filter_mode == "content":
                state.tree_filter_collapsed_dirs.add(entry.path.resolve())
            state.expanded.remove(entry.path.resolve())
            rebuild_tree_entries(preferred_path=entry.path.resolve())
            mark_tree_watch_dirty()
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
                    if state.tree_filter_active and state.tree_filter_mode == "content":
                        state.tree_filter_collapsed_dirs.add(resolved)
                    state.expanded.remove(resolved)
            else:
                if state.tree_filter_active and state.tree_filter_mode == "content":
                    state.tree_filter_collapsed_dirs.discard(resolved)
                state.expanded.add(resolved)
            rebuild_tree_entries(preferred_path=resolved)
            mark_tree_watch_dirty()
            preview_selected_entry()
            state.dirty = True
            return False

    prev_start = state.start
    prev_text_x = state.text_x
    scrolling_down = False
    page_rows = visible_content_rows()
    effective_max_start = _effective_max_start(state, page_rows)
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
            state.start = effective_max_start
        else:
            state.start = max(0, min(count - 1, effective_max_start))
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
        state.start = effective_max_start
    elif key == "ESC":
        return True

    state.start = max(0, min(state.start, effective_max_start))
    state.max_start = max(state.max_start, effective_max_start)
    grew_preview = scrolling_down and maybe_grow_directory_preview()
    if state.start != prev_start or state.text_x != prev_text_x or grew_preview:
        state.dirty = True
    return False
