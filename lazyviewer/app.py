from __future__ import annotations

import os
import shutil
import sys
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
from .highlight import colorize_source
from .input import read_key
from .preview import build_rendered_for_path
from .render import render_dual_page, render_help_page
from .state import AppState
from .terminal import TerminalController
from .tree import build_tree_entries, clamp_left_width, compute_left_width

DOUBLE_CLICK_SECONDS = 0.35


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

    tree_entries = build_tree_entries(tree_root, expanded, show_hidden)
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
    rendered = build_rendered_for_path(current_path, show_hidden, style, no_color)
    lines = build_screen_lines(rendered, right_width)
    max_start = max(0, len(lines) - usable)

    state = AppState(
        current_path=current_path,
        tree_root=tree_root,
        expanded=expanded,
        show_hidden=show_hidden,
        tree_entries=tree_entries,
        selected_idx=selected_idx,
        rendered=rendered,
        lines=lines,
        start=0,
        tree_start=0,
        text_x=0,
        left_width=left_width,
        right_width=right_width,
        usable=usable,
        max_start=max_start,
        last_right_width=right_width,
    )

    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    terminal = TerminalController(stdin_fd, stdout_fd)

    def refresh_rendered_for_current_path() -> None:
        state.rendered = build_rendered_for_path(state.current_path, state.show_hidden, style, no_color)
        state.lines = build_screen_lines(state.rendered, state.right_width)
        state.max_start = max(0, len(state.lines) - state.usable)
        state.start = 0
        state.text_x = 0

    def preview_selected_entry(force: bool = False) -> None:
        if not state.tree_entries:
            return
        entry = state.tree_entries[state.selected_idx]
        selected_target = entry.path.resolve()
        if not force and selected_target == state.current_path:
            return
        state.current_path = selected_target
        refresh_rendered_for_current_path()

    with terminal.raw_mode():
        while True:
            term = shutil.get_terminal_size((80, 24))
            state.usable = max(1, term.lines - 1)
            state.left_width = clamp_left_width(term.columns, state.left_width)
            state.right_width = max(1, term.columns - state.left_width - 2)
            if state.right_width != state.last_right_width:
                state.lines = build_screen_lines(state.rendered, state.right_width)
                state.last_right_width = state.right_width
                state.dirty = True
            state.max_start = max(0, len(state.lines) - state.usable)

            prev_tree_start = state.tree_start
            if state.selected_idx < state.tree_start:
                state.tree_start = state.selected_idx
            elif state.selected_idx >= state.tree_start + state.usable:
                state.tree_start = state.selected_idx - state.usable + 1
            state.tree_start = max(0, min(state.tree_start, max(0, len(state.tree_entries) - state.usable)))
            if state.tree_start != prev_tree_start:
                state.dirty = True

            if state.dirty:
                if state.show_help:
                    render_help_page(term.columns, term.lines)
                else:
                    render_dual_page(
                        state.lines,
                        state.start,
                        state.tree_entries,
                        state.tree_start,
                        state.selected_idx,
                        state.usable,
                        state.current_path,
                        state.tree_root,
                        state.expanded,
                        term.columns,
                        state.left_width,
                        state.text_x,
                        state.browser_visible,
                        state.show_hidden,
                    )
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
                key = "ENTER"
                state.skip_next_lf = False
            else:
                state.skip_next_lf = False

            if key.isdigit():
                state.count_buffer += key
                continue

            count = int(state.count_buffer) if state.count_buffer else None
            state.count_buffer = ""
            if key == "?":
                state.show_help = not state.show_help
                state.dirty = True
                continue
            if state.show_help:
                if key.lower() == "q" or key == "ESC" or key == "\x03":
                    state.show_help = False
                    state.dirty = True
                continue
            if key == "CTRL_U":
                old_root = state.tree_root.resolve()
                parent_root = old_root.parent.resolve()
                if parent_root != old_root:
                    state.tree_root = parent_root
                    state.expanded = {state.tree_root, old_root}
                    state.tree_entries = build_tree_entries(state.tree_root, state.expanded, state.show_hidden)
                    state.selected_idx = 0
                    for idx, entry in enumerate(state.tree_entries):
                        if entry.path.resolve() == old_root:
                            state.selected_idx = idx
                            break
                    state.tree_start = max(0, state.selected_idx - max(1, state.usable // 2))
                    state.dirty = True
                continue
            if key == ".":
                state.show_hidden = not state.show_hidden
                save_show_hidden(state.show_hidden)
                selected_path = (
                    state.tree_entries[state.selected_idx].path.resolve() if state.tree_entries else state.tree_root
                )
                state.tree_entries = build_tree_entries(state.tree_root, state.expanded, state.show_hidden)
                state.selected_idx = 0
                for idx, entry in enumerate(state.tree_entries):
                    if entry.path.resolve() == selected_path:
                        state.selected_idx = idx
                        break
                preview_selected_entry(force=True)
                state.dirty = True
                continue
            if key.lower() == "t":
                state.browser_visible = not state.browser_visible
                state.dirty = True
                continue
            if key.lower() == "e":
                edit_target: Path | None = None
                if state.browser_visible and state.tree_entries:
                    selected_entry = state.tree_entries[state.selected_idx]
                    if not selected_entry.is_dir and selected_entry.path.is_file():
                        edit_target = selected_entry.path.resolve()
                if edit_target is None and state.current_path.is_file():
                    edit_target = state.current_path.resolve()
                if edit_target is None:
                    state.rendered = "\033[31m<cannot edit a directory>\033[0m"
                    state.lines = build_screen_lines(state.rendered, state.right_width)
                    state.max_start = max(0, len(state.lines) - state.usable)
                    state.start = 0
                    state.text_x = 0
                    state.dirty = True
                    continue

                error = launch_editor(edit_target, terminal.disable_tui_mode, terminal.enable_tui_mode)
                state.current_path = edit_target
                if error is None:
                    state.rendered = build_rendered_for_path(
                        state.current_path,
                        state.show_hidden,
                        style,
                        no_color,
                    )
                else:
                    state.rendered = f"\033[31m{error}\033[0m"
                state.lines = build_screen_lines(state.rendered, state.right_width)
                state.max_start = max(0, len(state.lines) - state.usable)
                state.start = 0
                state.text_x = 0
                state.dirty = True
                continue
            if key.lower() == "q" or key == "\x03":
                break
            if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
                direction = -1 if key.startswith("MOUSE_WHEEL_UP:") else 1
                parts = key.split(":")
                col: int | None = None
                if len(parts) >= 3:
                    try:
                        col = int(parts[1])
                    except Exception:
                        col = None
                if state.browser_visible and col is not None and col <= state.left_width:
                    prev_selected = state.selected_idx
                    state.selected_idx = max(0, min(len(state.tree_entries) - 1, state.selected_idx + direction))
                    preview_selected_entry()
                    if state.selected_idx != prev_selected:
                        state.dirty = True
                else:
                    prev_start = state.start
                    state.start += direction * 3
                    state.start = max(0, min(state.start, state.max_start))
                    if state.start != prev_start:
                        state.dirty = True
                continue
            if key.startswith("MOUSE_LEFT_DOWN:"):
                parts = key.split(":")
                if len(parts) >= 3:
                    try:
                        col = int(parts[1])
                        row = int(parts[2])
                    except Exception:
                        col = None
                        row = None
                    if (
                        state.browser_visible
                        and col is not None
                        and row is not None
                        and 1 <= row <= state.usable
                        and col <= state.left_width
                    ):
                        clicked_idx = state.tree_start + (row - 1)
                        if 0 <= clicked_idx < len(state.tree_entries):
                            prev_selected = state.selected_idx
                            state.selected_idx = clicked_idx
                            preview_selected_entry()
                            if state.selected_idx != prev_selected:
                                state.dirty = True
                            now = time.monotonic()
                            is_double = (
                                clicked_idx == state.last_click_idx
                                and (now - state.last_click_time) <= DOUBLE_CLICK_SECONDS
                            )
                            state.last_click_idx = clicked_idx
                            state.last_click_time = now
                            if is_double:
                                entry = state.tree_entries[state.selected_idx]
                                if entry.is_dir:
                                    resolved = entry.path.resolve()
                                    if resolved in state.expanded:
                                        state.expanded.remove(resolved)
                                    else:
                                        state.expanded.add(resolved)
                                    state.tree_entries = build_tree_entries(
                                        state.tree_root,
                                        state.expanded,
                                        state.show_hidden,
                                    )
                                    state.selected_idx = min(state.selected_idx, len(state.tree_entries) - 1)
                                    state.dirty = True
                                else:
                                    state.current_path = entry.path.resolve()
                                    state.rendered = build_rendered_for_path(
                                        state.current_path,
                                        state.show_hidden,
                                        style,
                                        no_color,
                                    )
                                    state.lines = build_screen_lines(state.rendered, state.right_width)
                                    state.max_start = max(0, len(state.lines) - state.usable)
                                    state.start = 0
                                    state.text_x = 0
                                    state.dirty = True
                continue
            if key == "SHIFT_LEFT":
                prev_left = state.left_width
                state.left_width = clamp_left_width(term.columns, state.left_width - 2)
                if state.left_width != prev_left:
                    save_left_pane_percent(term.columns, state.left_width)
                    state.right_width = max(1, term.columns - state.left_width - 2)
                    if state.right_width != state.last_right_width:
                        state.lines = build_screen_lines(state.rendered, state.right_width)
                        state.last_right_width = state.right_width
                        state.max_start = max(0, len(state.lines) - state.usable)
                        state.start = min(state.start, state.max_start)
                    state.dirty = True
                continue
            if key == "SHIFT_RIGHT":
                prev_left = state.left_width
                state.left_width = clamp_left_width(term.columns, state.left_width + 2)
                if state.left_width != prev_left:
                    save_left_pane_percent(term.columns, state.left_width)
                    state.right_width = max(1, term.columns - state.left_width - 2)
                    if state.right_width != state.last_right_width:
                        state.lines = build_screen_lines(state.rendered, state.right_width)
                        state.last_right_width = state.right_width
                        state.max_start = max(0, len(state.lines) - state.usable)
                        state.start = min(state.start, state.max_start)
                    state.dirty = True
                continue

            if state.browser_visible and key.lower() == "j":
                prev_selected = state.selected_idx
                state.selected_idx = min(len(state.tree_entries) - 1, state.selected_idx + 1)
                preview_selected_entry()
                if state.selected_idx != prev_selected:
                    state.dirty = True
                continue
            if state.browser_visible and key.lower() == "k":
                prev_selected = state.selected_idx
                state.selected_idx = max(0, state.selected_idx - 1)
                preview_selected_entry()
                if state.selected_idx != prev_selected:
                    state.dirty = True
                continue
            if state.browser_visible and key.lower() == "l":
                entry = state.tree_entries[state.selected_idx]
                if entry.is_dir:
                    resolved = entry.path.resolve()
                    if resolved not in state.expanded:
                        state.expanded.add(resolved)
                        state.tree_entries = build_tree_entries(state.tree_root, state.expanded, state.show_hidden)
                        state.selected_idx = min(state.selected_idx, len(state.tree_entries) - 1)
                        preview_selected_entry()
                        state.dirty = True
                    else:
                        next_idx = state.selected_idx + 1
                        if next_idx < len(state.tree_entries) and state.tree_entries[next_idx].depth > entry.depth:
                            state.selected_idx = next_idx
                            preview_selected_entry()
                            state.dirty = True
                else:
                    state.current_path = entry.path.resolve()
                    state.rendered = build_rendered_for_path(
                        state.current_path,
                        state.show_hidden,
                        style,
                        no_color,
                    )
                    state.lines = build_screen_lines(state.rendered, state.right_width)
                    state.max_start = max(0, len(state.lines) - state.usable)
                    state.start = 0
                    state.text_x = 0
                    state.dirty = True
                continue
            if state.browser_visible and key.lower() == "h":
                entry = state.tree_entries[state.selected_idx]
                if (
                    entry.is_dir
                    and entry.path.resolve() in state.expanded
                    and entry.path.resolve() != state.tree_root
                ):
                    state.expanded.remove(entry.path.resolve())
                    state.tree_entries = build_tree_entries(state.tree_root, state.expanded, state.show_hidden)
                    state.selected_idx = min(state.selected_idx, len(state.tree_entries) - 1)
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
                continue
            if state.browser_visible and key == "ENTER":
                entry = state.tree_entries[state.selected_idx]
                if entry.is_dir:
                    resolved = entry.path.resolve()
                    if resolved in state.expanded:
                        if resolved != state.tree_root:
                            state.expanded.remove(resolved)
                    else:
                        state.expanded.add(resolved)
                    state.tree_entries = build_tree_entries(state.tree_root, state.expanded, state.show_hidden)
                    state.selected_idx = min(state.selected_idx, len(state.tree_entries) - 1)
                    preview_selected_entry()
                    state.dirty = True
                    continue

            prev_start = state.start
            prev_text_x = state.text_x
            if key == " " or key.lower() == "f":
                pages = count if count is not None else 1
                state.start += state.usable * max(1, pages)
            elif key.lower() == "d":
                mult = count if count is not None else 1
                state.start += max(1, state.usable // 2) * max(1, mult)
            elif key.lower() == "u":
                mult = count if count is not None else 1
                state.start -= max(1, state.usable // 2) * max(1, mult)
            elif key == "DOWN" or (not state.browser_visible and key.lower() == "j"):
                state.start += count if count is not None else 1
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
            elif key == "ENTER":
                state.start += count if count is not None else 1
            elif key == "B":
                pages = count if count is not None else 1
                state.start -= state.usable * max(1, pages)
            elif key == "LEFT" or (not state.browser_visible and key.lower() == "h"):
                step = count if count is not None else 4
                state.text_x = max(0, state.text_x - max(1, step))
            elif key == "RIGHT" or (not state.browser_visible and key.lower() == "l"):
                step = count if count is not None else 4
                state.text_x += max(1, step)
            elif key == "HOME":
                state.start = 0
            elif key == "END":
                state.start = state.max_start
            elif key == "ESC":
                break

            state.start = max(0, min(state.start, state.max_start))
            if state.start != prev_start or state.text_x != prev_text_x:
                state.dirty = True
