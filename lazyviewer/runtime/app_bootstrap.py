"""Initial runtime state bootstrap for ``run_pager``."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..render.ansi import build_screen_lines
from .screen import _centered_scroll_start, _first_git_change_screen_line
from .state import AppState
from ..ui_theme import resolve_theme


def _normalize_workspace_roots(path: Path, workspace_paths: list[Path] | None) -> list[Path]:
    """Resolve startup workspace roots from user-provided paths."""
    candidates = list(workspace_paths) if workspace_paths else [path]
    roots: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if not resolved.exists():
            continue
        root = resolved if resolved.is_dir() else resolved.parent
        roots.append(root.resolve())

    if roots:
        return roots

    resolved_path = path.resolve()
    fallback_root = resolved_path if resolved_path.is_dir() else resolved_path.parent
    return [fallback_root.resolve()]


@dataclass(frozen=True)
class AppStateBootstrap:
    """State bootstrap object that builds initial application state."""

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
    theme_name: str = "default"

    def build_state(
        self,
        path: Path,
        style: str,
        no_color: bool,
        workspace_paths: list[Path] | None = None,
    ) -> AppState:
        """Create initial ``AppState`` from path and persisted preferences."""
        initial_path = path.resolve()
        current_path = initial_path
        tree_roots = _normalize_workspace_roots(initial_path, workspace_paths)
        tree_root = tree_roots[0]
        workspace_expanded = [{root} for root in tree_roots]
        expanded: set[Path] = {candidate for section in workspace_expanded for candidate in section}
        show_hidden = self.load_show_hidden()
        named_marks = self.load_named_marks()

        tree_entries = []
        for section_idx, section_root in enumerate(tree_roots):
            tree_entries.extend(
                self.build_tree_entries(
                    section_root,
                    workspace_expanded[section_idx],
                    show_hidden,
                    skip_gitignored=self.skip_gitignored_for_hidden_mode(show_hidden),
                    workspace_root=section_root,
                    workspace_section=section_idx,
                )
            )
        selected_path = current_path if current_path.exists() else tree_root
        preferred_selected_idx = next(
            (
                idx
                for idx, entry in enumerate(tree_entries)
                if entry.path.resolve() == selected_path.resolve() and entry.workspace_section == 0
            ),
            None,
        )
        selected_idx = (
            preferred_selected_idx
            if preferred_selected_idx is not None
            else next(
                (
                    idx
                    for idx, entry in enumerate(tree_entries)
                    if entry.path.resolve() == selected_path.resolve()
                ),
                0,
            )
        )

        term = shutil.get_terminal_size((80, 24))
        usable = max(1, term.lines - 1)
        saved_percent = self.load_left_pane_percent()
        if saved_percent is None:
            initial_left = self.compute_left_width(term.columns)
        else:
            initial_left = int((saved_percent / 100.0) * term.columns)
        left_width = self.clamp_left_width(term.columns, initial_left)
        right_width = max(1, term.columns - left_width - 1)
        initial_dir_max_entries = min(
            self.dir_preview_initial_max_entries,
            max(1, usable - 4),
        )
        initial_render = self.build_rendered_for_path(
            current_path,
            show_hidden,
            style,
            no_color,
            dir_max_entries=initial_dir_max_entries,
            dir_skip_gitignored=self.skip_gitignored_for_hidden_mode(show_hidden),
            prefer_git_diff=self.git_features_default_enabled,
            dir_show_size_labels=self.tree_size_labels_default_enabled,
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
            tree_roots=tree_roots,
            workspace_expanded=workspace_expanded,
            theme_name=self.theme_name,
            theme=resolve_theme(self.theme_name, no_color=no_color),
            expanded=expanded,
            tree_render_expanded=set(expanded),
            show_hidden=show_hidden,
            show_tree_sizes=self.tree_size_labels_default_enabled,
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
            dir_preview_max_entries=initial_dir_max_entries,
            dir_preview_truncated=initial_render.truncated,
            dir_preview_path=current_path if initial_render.is_directory else None,
            preview_image_path=initial_render.image_path,
            preview_image_format=initial_render.image_format,
            preview_is_git_diff=initial_render.is_git_diff_preview,
            git_features_enabled=self.git_features_default_enabled,
            named_marks=named_marks,
        )

__all__ = ["AppStateBootstrap"]
