"""Git-modified file navigation helpers for app runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..state import AppState
from .screen import (
    _centered_scroll_start,
    _git_change_block_start_lines,
    _tree_order_key_for_relative_path,
)


def _sorted_git_modified_file_paths(state: AppState) -> list[Path]:
    """Return git-modified file paths under tree root in tree display order."""
    if not state.git_features_enabled:
        return []
    if not state.git_status_overlay:
        return []

    root = state.tree_root.resolve()
    rel_to_path: dict[Path, Path] = {}
    for raw_path, flags in state.git_status_overlay.items():
        if flags == 0:
            continue
        path = raw_path.resolve()
        if path == root or not path.is_relative_to(root):
            continue
        if not path.exists() or path.is_dir():
            continue
        try:
            rel = path.relative_to(root)
        except Exception:
            continue
        if not state.show_hidden and any(part.startswith(".") for part in rel.parts):
            continue
        rel_to_path[rel] = path

    if not rel_to_path:
        return []
    ordered_rel = sorted(rel_to_path, key=_tree_order_key_for_relative_path)
    return [rel_to_path[rel] for rel in ordered_rel]


@dataclass(frozen=True)
class GitModifiedJumpDeps:
    """Dependency bundle for jumping across git-modified locations."""

    state: AppState
    visible_content_rows: Callable[[], int]
    refresh_git_status_overlay: Callable[..., None]
    current_jump_location: Callable[[], object]
    jump_to_path: Callable[[Path], None]
    record_jump_if_changed: Callable[[object], None]
    clear_status_message: Callable[[], None]
    set_status_message: Callable[[str], None]

    def jump_to_next_git_modified(
        self,
        direction: int,
    ) -> bool:
        """Jump to next/previous git change block or modified file.

        The method first tries intra-file diff blocks when viewing a git diff.
        If no further block exists in direction, it falls back to modified-file
        navigation, wrapping at file boundaries and reporting wrap status.
        """
        state = self.state
        if direction == 0:
            return False
        self.clear_status_message()

        same_file_change_blocks: list[int] = []
        if state.preview_is_git_diff and state.current_path.is_file():
            same_file_change_blocks = _git_change_block_start_lines(state.lines)
            if same_file_change_blocks:
                probe_line = state.start + max(0, self.visible_content_rows() // 3)
                current_block: int | None = None
                for line_idx in same_file_change_blocks:
                    if line_idx <= probe_line:
                        current_block = line_idx
                    else:
                        break

                target_line: int | None = None
                if direction > 0:
                    if current_block is None:
                        target_line = same_file_change_blocks[0]
                    else:
                        for line_idx in same_file_change_blocks:
                            if line_idx > current_block:
                                target_line = line_idx
                                break
                else:
                    if current_block is not None:
                        for line_idx in reversed(same_file_change_blocks):
                            if line_idx < current_block:
                                target_line = line_idx
                                break

                if target_line is not None:
                    next_start = _centered_scroll_start(
                        target_line,
                        state.max_start,
                        self.visible_content_rows(),
                    )
                    if next_start != state.start:
                        state.start = next_start
                        return True

        self.refresh_git_status_overlay()
        modified_paths = _sorted_git_modified_file_paths(state)
        if not modified_paths:
            return False

        root = state.tree_root.resolve()
        if state.browser_visible and state.tree_entries and 0 <= state.selected_idx < len(state.tree_entries):
            anchor_path = state.tree_entries[state.selected_idx].path.resolve()
        else:
            anchor_path = state.current_path.resolve()

        ordered_items: list[tuple[tuple[tuple[int, str, str], ...], Path]] = []
        for path in modified_paths:
            rel_path = path.relative_to(root)
            ordered_items.append((_tree_order_key_for_relative_path(rel_path), path))

        try:
            anchor_rel_path = anchor_path.relative_to(root)
            anchor_key: tuple[tuple[int, str, str], ...] | None = _tree_order_key_for_relative_path(
                anchor_rel_path,
                is_dir=anchor_path.is_dir(),
            )
        except Exception:
            anchor_key = None

        target: Path | None = None
        wrapped_files = False
        if direction > 0:
            if anchor_key is not None:
                for item_key, path in ordered_items:
                    if item_key > anchor_key:
                        target = path
                        break
                if target is None:
                    wrapped_files = True
            if target is None:
                target = ordered_items[0][1]
        else:
            if anchor_key is not None:
                for item_key, path in reversed(ordered_items):
                    if item_key < anchor_key:
                        target = path
                        break
                if target is None:
                    wrapped_files = True
            if target is None:
                target = ordered_items[-1][1]

        if target is None:
            return False

        if target == anchor_path and same_file_change_blocks:
            wrap_line = same_file_change_blocks[0] if direction > 0 else same_file_change_blocks[-1]
            next_start = _centered_scroll_start(
                wrap_line,
                state.max_start,
                self.visible_content_rows(),
            )
            state.start = next_start
            self.set_status_message("wrapped to first change" if direction > 0 else "wrapped to last change")
            return True

        if target == anchor_path:
            return False

        origin = self.current_jump_location()
        self.jump_to_path(target)
        target_change_blocks: list[int] = []
        if state.preview_is_git_diff and state.current_path.is_file():
            target_change_blocks = _git_change_block_start_lines(state.lines)
        if target_change_blocks:
            target_line = target_change_blocks[0] if direction > 0 else target_change_blocks[-1]
            state.start = _centered_scroll_start(
                target_line,
                state.max_start,
                self.visible_content_rows(),
            )
        self.record_jump_if_changed(origin)
        if wrapped_files:
            self.set_status_message("wrapped to first change" if direction > 0 else "wrapped to last change")
        return True
