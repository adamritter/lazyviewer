"""Shared helper functions used by ``runtime.app`` composition."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable

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
