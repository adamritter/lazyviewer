"""Shared helper functions used by ``runtime.app`` composition."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable

from ..source_pane import SourcePane
from .screen import _centered_scroll_start, _first_git_change_screen_line
from .state import AppState
from .terminal import TerminalController

WRAP_STATUS_SECONDS = 1.2


def skip_gitignored_for_hidden_mode(show_hidden: bool) -> bool:
    """Return whether gitignored paths should be excluded for current visibility mode."""
    # Hidden mode should reveal both dotfiles and gitignored paths.
    return not show_hidden


def copy_text_to_clipboard(text: str) -> bool:
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


def clear_status_message(state: AppState) -> None:
    """Clear transient status message and its expiration timestamp."""
    state.status_message = ""
    state.status_message_until = 0.0


def set_status_message(state: AppState, message: str) -> None:
    """Set transient status message visible for a fixed short interval."""
    state.status_message = message
    state.status_message_until = time.monotonic() + WRAP_STATUS_SECONDS


def clear_source_selection(state: AppState) -> bool:
    """Clear source text selection anchors, returning whether anything changed."""
    changed = state.source_selection_anchor is not None or state.source_selection_focus is not None
    state.source_selection_anchor = None
    state.source_selection_focus = None
    return changed


def refresh_rendered_for_current_path(
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
        SourcePane.clear_directory_preview_cache()
        SourcePane.clear_diff_preview_cache()
    resolved_target = state.current_path.resolve()
    is_dir_target = resolved_target.is_dir()
    if is_dir_target:
        if reset_dir_budget or state.dir_preview_path != resolved_target:
            state.dir_preview_max_entries = SourcePane.DIR_PREVIEW_INITIAL_MAX_ENTRIES
        dir_limit = state.dir_preview_max_entries
    else:
        dir_limit = SourcePane.DIR_PREVIEW_INITIAL_MAX_ENTRIES

    prefer_git_diff = state.git_features_enabled and not (
        state.tree_filter_active
        and state.tree_filter_mode == "content"
        and bool(state.tree_filter_query)
    )
    rendered_for_path = SourcePane.build_rendered_for_path(
        state.current_path,
        state.show_hidden,
        style,
        no_color,
        dir_max_entries=dir_limit,
        dir_skip_gitignored=skip_gitignored_for_hidden_mode(state.show_hidden),
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


def maybe_grow_directory_preview(
    state: AppState,
    visible_content_rows: Callable[[], int],
    refresh_rendered_for_current_path_fn: Callable[..., None],
) -> bool:
    """Expand directory preview budget when scrolling near truncated preview end."""
    if state.dir_preview_path is None or not state.dir_preview_truncated:
        return False
    if state.current_path.resolve() != state.dir_preview_path:
        return False
    if state.dir_preview_max_entries >= SourcePane.DIR_PREVIEW_HARD_MAX_ENTRIES:
        return False

    # Only grow when the user is effectively at the end of the current preview.
    near_end_threshold = max(1, visible_content_rows() // 3)
    if state.start < max(0, state.max_start - near_end_threshold):
        return False

    previous_line_count = len(state.lines)
    state.dir_preview_max_entries = min(
        SourcePane.DIR_PREVIEW_HARD_MAX_ENTRIES,
        state.dir_preview_max_entries + SourcePane.DIR_PREVIEW_GROWTH_STEP,
    )
    refresh_rendered_for_current_path_fn(reset_scroll=False, reset_dir_budget=False)
    return len(state.lines) > previous_line_count


def toggle_git_features(
    state: AppState,
    refresh_git_status_overlay: Callable[..., None],
    refresh_rendered_for_current_path_fn: Callable[..., None],
) -> None:
    """Toggle git-aware features and refresh overlays/rendering accordingly."""
    state.git_features_enabled = not state.git_features_enabled
    if state.git_features_enabled:
        refresh_git_status_overlay(force=True)
    else:
        if state.git_status_overlay:
            state.git_status_overlay = {}
        state.git_status_last_refresh = time.monotonic()
    refresh_rendered_for_current_path_fn(
        reset_scroll=state.git_features_enabled,
        reset_dir_budget=False,
    )
    state.dirty = True


def toggle_tree_size_labels(
    state: AppState,
    refresh_rendered_for_current_path_fn: Callable[..., None],
) -> None:
    """Toggle directory size labels in preview and refresh when relevant."""
    state.show_tree_sizes = not state.show_tree_sizes
    if state.current_path.resolve().is_dir():
        refresh_rendered_for_current_path_fn(reset_scroll=False, reset_dir_budget=False)
    state.dirty = True


def launch_lazygit(
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
