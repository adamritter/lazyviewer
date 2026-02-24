"""Source pane runtime façade used by the application layer."""

from __future__ import annotations

from collections.abc import Callable
import os
import shutil
from pathlib import Path

from ..runtime.state import AppState
from ..tree_model.rendering import TREE_SIZE_LABEL_MIN_BYTES
from .diff import clear_diff_preview_cache as _clear_diff_preview_cache
from .directory import (
    DIR_PREVIEW_CACHE_MAX,
    DIR_PREVIEW_DEFAULT_DEPTH,
    DIR_PREVIEW_GROWTH_STEP,
    DIR_PREVIEW_HARD_MAX_ENTRIES,
    DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    _DIR_PREVIEW_CACHE,
    build_directory_preview as _build_directory_preview,
    clear_directory_preview_cache as _clear_directory_preview_cache,
)
from .interaction.events import (
    directory_preview_target_for_display_line as _directory_preview_target_for_display_line,
)
from .interaction.geometry import (
    SourcePaneGeometry,
    copy_selected_source_range as _copy_selected_source_range,
)
from .interaction.mouse import SourcePaneClickResult, SourcePaneMouseHandlers
from .path import BINARY_PROBE_BYTES, COLORIZE_MAX_FILE_BYTES, PNG_SIGNATURE, RenderedPath
from .renderer import SourcePaneRenderer
from .syntax import colorize_source as _colorize_source


class SourcePane:
    """App-owned source pane object and package façade."""

    DIR_PREVIEW_DEFAULT_DEPTH = DIR_PREVIEW_DEFAULT_DEPTH
    DIR_PREVIEW_INITIAL_MAX_ENTRIES = DIR_PREVIEW_INITIAL_MAX_ENTRIES
    DIR_PREVIEW_GROWTH_STEP = DIR_PREVIEW_GROWTH_STEP
    DIR_PREVIEW_HARD_MAX_ENTRIES = DIR_PREVIEW_HARD_MAX_ENTRIES
    DIR_PREVIEW_CACHE_MAX = DIR_PREVIEW_CACHE_MAX
    TREE_SIZE_LABEL_MIN_BYTES = TREE_SIZE_LABEL_MIN_BYTES
    BINARY_PROBE_BYTES = BINARY_PROBE_BYTES
    COLORIZE_MAX_FILE_BYTES = COLORIZE_MAX_FILE_BYTES
    PNG_SIGNATURE = PNG_SIGNATURE
    RenderedPath = RenderedPath
    SourcePaneClickResult = SourcePaneClickResult
    SourcePaneMouseHandlers = SourcePaneMouseHandlers
    SourcePaneGeometry = SourcePaneGeometry
    SourcePaneRenderer = SourcePaneRenderer
    _DIR_PREVIEW_CACHE = _DIR_PREVIEW_CACHE

    @staticmethod
    def colorize_source(source: str, target: Path, style: str) -> str:
        """Colorize source text for terminal preview."""
        return _colorize_source(source, target, style)

    @staticmethod
    def build_rendered_for_path(
        target: Path,
        show_hidden: bool,
        style: str,
        no_color: bool,
        dir_max_depth: int = DIR_PREVIEW_DEFAULT_DEPTH,
        dir_max_entries: int = DIR_PREVIEW_INITIAL_MAX_ENTRIES,
        dir_skip_gitignored: bool = False,
        prefer_git_diff: bool = True,
        dir_git_status_overlay: dict[Path, int] | None = None,
        dir_show_size_labels: bool = True,
    ) -> RenderedPath:
        """Build source-pane preview payload for one filesystem path."""
        return RenderedPath.from_path(
            target,
            show_hidden,
            style,
            no_color,
            dir_max_depth=dir_max_depth,
            dir_max_entries=dir_max_entries,
            dir_skip_gitignored=dir_skip_gitignored,
            prefer_git_diff=prefer_git_diff,
            dir_git_status_overlay=dir_git_status_overlay,
            dir_show_size_labels=dir_show_size_labels,
            colorize_source_fn=SourcePane.colorize_source,
        )

    @staticmethod
    def build_directory_preview(
        root_dir: Path,
        show_hidden: bool,
        max_depth: int = DIR_PREVIEW_DEFAULT_DEPTH,
        max_entries: int = DIR_PREVIEW_INITIAL_MAX_ENTRIES,
        skip_gitignored: bool = False,
        git_status_overlay: dict[Path, int] | None = None,
        show_size_labels: bool = True,
    ) -> tuple[str, bool]:
        """Render a directory preview tree and return ``(text, truncated)``."""
        return _build_directory_preview(
            root_dir,
            show_hidden,
            max_depth=max_depth,
            max_entries=max_entries,
            skip_gitignored=skip_gitignored,
            git_status_overlay=git_status_overlay,
            show_size_labels=show_size_labels,
        )

    @staticmethod
    def clear_directory_preview_cache() -> None:
        """Clear cached directory previews and doc-summary cache."""
        _clear_directory_preview_cache()

    @staticmethod
    def clear_diff_preview_cache() -> None:
        """Clear cached git-diff preview payloads."""
        _clear_diff_preview_cache()

    @staticmethod
    def copy_selected_source_range(
        state: AppState,
        start_pos: tuple[int, int],
        end_pos: tuple[int, int],
        copy_text_to_clipboard: Callable[[str], bool],
    ) -> bool:
        """Copy one selected source range to clipboard."""
        return _copy_selected_source_range(
            state,
            start_pos,
            end_pos,
            copy_text_to_clipboard=copy_text_to_clipboard,
        )

    @staticmethod
    def directory_preview_target_for_display_line(
        state: AppState,
        display_line: int,
    ) -> Path | None:
        """Map one rendered preview display row to an underlying filesystem path."""
        return _directory_preview_target_for_display_line(state, display_line)

    def __init__(
        self,
        *,
        state: AppState,
        visible_content_rows: Callable[[], int],
        move_tree_selection: Callable[[int], bool],
        maybe_grow_directory_preview: Callable[[], bool],
        clear_source_selection: Callable[[], bool],
        copy_selected_source_range: Callable[[tuple[int, int], tuple[int, int]], bool],
        directory_preview_target_for_display_line: Callable[[int], Path | None],
        open_tree_filter: Callable[[str], None],
        apply_tree_filter_query: Callable[..., None],
        jump_to_path: Callable[[Path], None],
        get_terminal_size: Callable[[tuple[int, int]], os.terminal_size] = shutil.get_terminal_size,
    ) -> None:
        self.state = state
        self._move_tree_selection = move_tree_selection
        self._maybe_grow_directory_preview = maybe_grow_directory_preview
        self.geometry = SourcePaneGeometry(
            state,
            visible_content_rows,
            get_terminal_size=get_terminal_size,
        )
        self.mouse = SourcePaneMouseHandlers(
            state=state,
            visible_content_rows=visible_content_rows,
            source_pane_col_bounds=self.geometry.source_pane_col_bounds,
            source_selection_position=self.geometry.source_selection_position,
            directory_preview_target_for_display_line=directory_preview_target_for_display_line,
            max_horizontal_text_offset=self.geometry.max_horizontal_text_offset,
            maybe_grow_directory_preview=maybe_grow_directory_preview,
            clear_source_selection=clear_source_selection,
            copy_selected_source_range=copy_selected_source_range,
            open_tree_filter=open_tree_filter,
            apply_tree_filter_query=apply_tree_filter_query,
            jump_to_path=jump_to_path,
        )

    @staticmethod
    def _parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
        parts = mouse_key.split(":")
        if len(parts) < 3:
            return None, None
        try:
            return int(parts[1]), int(parts[2])
        except Exception:
            return None, None

    def handle_tree_mouse_wheel(self, mouse_key: str) -> bool:
        is_vertical = mouse_key.startswith("MOUSE_WHEEL_UP:") or mouse_key.startswith("MOUSE_WHEEL_DOWN:")
        is_horizontal = mouse_key.startswith("MOUSE_WHEEL_LEFT:") or mouse_key.startswith("MOUSE_WHEEL_RIGHT:")
        if not (is_vertical or is_horizontal):
            return False

        col, _row = self._parse_mouse_col_row(mouse_key)
        in_tree_pane = self.state.browser_visible and col is not None and col <= self.state.left_width

        if is_horizontal:
            if in_tree_pane:
                return True
            prev_text_x = self.state.text_x
            if mouse_key.startswith("MOUSE_WHEEL_LEFT:"):
                self.state.text_x = max(0, self.state.text_x - 4)
            else:
                self.state.text_x = min(self.geometry.max_horizontal_text_offset(), self.state.text_x + 4)
            if self.state.text_x != prev_text_x:
                self.state.dirty = True
            return True

        direction = -1 if mouse_key.startswith("MOUSE_WHEEL_UP:") else 1
        if in_tree_pane:
            if self._move_tree_selection(direction):
                self.state.dirty = True
            return True

        prev_start = self.state.start
        self.state.start += direction * 3
        self.state.start = max(0, min(self.state.start, self.state.max_start))
        grew_preview = direction > 0 and self._maybe_grow_directory_preview()
        if self.state.start != prev_start or grew_preview:
            self.state.dirty = True
        return True

    def handle_tree_mouse_click(self, mouse_key: str) -> SourcePaneClickResult:
        is_left_down = mouse_key.startswith("MOUSE_LEFT_DOWN:")
        is_left_up = mouse_key.startswith("MOUSE_LEFT_UP:")
        if not (is_left_down or is_left_up):
            return SourcePaneClickResult(handled=False)
        col, row = self._parse_mouse_col_row(mouse_key)
        if col is None or row is None:
            return SourcePaneClickResult(handled=True)
        return self.mouse.handle_click(
            col=col,
            row=row,
            is_left_down=is_left_down,
            is_left_up=is_left_up,
        )

    def tick_source_selection_drag(self) -> None:
        self.mouse.tick_source_selection_drag()
