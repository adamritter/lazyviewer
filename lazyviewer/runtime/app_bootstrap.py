"""Initial runtime state bootstrap for ``run_pager``."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..render.ansi import build_screen_lines
from .screen import _centered_scroll_start, _first_git_change_screen_line
from .state import AppState


@dataclass(frozen=True)
class AppStateBootstrapDeps:
    """Dependencies required to build the initial application state."""

    skip_gitignored_for_hidden_mode: Callable[[bool], bool]
    load_show_hidden: Callable[[], bool]
    load_named_marks: Callable[[], dict[str, object]]
    load_left_pane_percent: Callable[[], float | None]
    compute_left_width: Callable[[int], int]
    clamp_left_width: Callable[[int, int], int]
    build_tree_entries: Callable[..., list]
    build_rendered_for_path: Callable[..., object]
    git_features_default_enabled: bool
    tree_size_labels_default_enabled: bool
    dir_preview_initial_max_entries: int


def build_initial_app_state(
    path: Path,
    style: str,
    no_color: bool,
    deps: AppStateBootstrapDeps,
) -> AppState:
    """Create initial ``AppState`` from path and persisted preferences."""
    initial_path = path.resolve()
    current_path = initial_path
    tree_root = initial_path if initial_path.is_dir() else initial_path.parent
    expanded: set[Path] = {tree_root.resolve()}
    show_hidden = deps.load_show_hidden()
    named_marks = deps.load_named_marks()

    tree_entries = deps.build_tree_entries(
        tree_root,
        expanded,
        show_hidden,
        skip_gitignored=deps.skip_gitignored_for_hidden_mode(show_hidden),
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
    saved_percent = deps.load_left_pane_percent()
    if saved_percent is None:
        initial_left = deps.compute_left_width(term.columns)
    else:
        initial_left = int((saved_percent / 100.0) * term.columns)
    left_width = deps.clamp_left_width(term.columns, initial_left)
    right_width = max(1, term.columns - left_width - 2)
    initial_render = deps.build_rendered_for_path(
        current_path,
        show_hidden,
        style,
        no_color,
        dir_max_entries=deps.dir_preview_initial_max_entries,
        dir_skip_gitignored=deps.skip_gitignored_for_hidden_mode(show_hidden),
        prefer_git_diff=deps.git_features_default_enabled,
        dir_show_size_labels=deps.tree_size_labels_default_enabled,
    )
    rendered = initial_render.text
    lines = build_screen_lines(rendered, right_width, wrap=False)
    max_start = max(0, len(lines) - usable)
    initial_start = 0
    if initial_render.is_git_diff_preview:
        first_change = _first_git_change_screen_line(lines)
        if first_change is not None:
            initial_start = _centered_scroll_start(first_change, max_start, usable)

    return AppState(
        current_path=current_path,
        tree_root=tree_root,
        expanded=expanded,
        tree_render_expanded=set(expanded),
        show_hidden=show_hidden,
        show_tree_sizes=deps.tree_size_labels_default_enabled,
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
        dir_preview_max_entries=deps.dir_preview_initial_max_entries,
        dir_preview_truncated=initial_render.truncated,
        dir_preview_path=current_path if initial_render.is_directory else None,
        preview_image_path=initial_render.image_path,
        preview_image_format=initial_render.image_format,
        preview_is_git_diff=initial_render.is_git_diff_preview,
        git_features_enabled=deps.git_features_default_enabled,
        named_marks=named_marks,
    )


__all__ = ["AppStateBootstrapDeps", "build_initial_app_state"]
