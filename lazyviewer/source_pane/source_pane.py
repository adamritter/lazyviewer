"""Source pane runtime façade used by the application layer."""

from __future__ import annotations

from collections.abc import Callable
import os
import shutil
from pathlib import Path

from ..runtime.screen import _centered_scroll_start, _first_git_change_screen_line
from ..runtime.state import AppState
from .diff import clear_diff_preview_cache
from .directory import DirectoryPreview
from .interaction.events import (
    directory_preview_target_for_display_line as _directory_preview_target_for_display_line,
)
from .interaction.geometry import (
    SourcePaneGeometry,
    copy_selected_source_range as _copy_selected_source_range,
)
from .interaction.mouse import SourcePaneClickResult, SourcePaneMouseHandlers
from .path import RenderedPath, RenderedPathPreview
from .renderer import SourcePaneRenderer


class SourcePane:
    """Runtime source-pane controller and compatibility facade."""

    DIR_PREVIEW_DEFAULT_DEPTH = DirectoryPreview.DIR_PREVIEW_DEFAULT_DEPTH
    DIR_PREVIEW_INITIAL_MAX_ENTRIES = DirectoryPreview.DIR_PREVIEW_INITIAL_MAX_ENTRIES
    DIR_PREVIEW_GROWTH_STEP = DirectoryPreview.DIR_PREVIEW_GROWTH_STEP
    DIR_PREVIEW_HARD_MAX_ENTRIES = DirectoryPreview.DIR_PREVIEW_HARD_MAX_ENTRIES
    DIR_PREVIEW_CACHE_MAX = DirectoryPreview.DIR_PREVIEW_CACHE_MAX
    TREE_SIZE_LABEL_MIN_BYTES = DirectoryPreview.TREE_SIZE_LABEL_MIN_BYTES
    BINARY_PROBE_BYTES = RenderedPathPreview.BINARY_PROBE_BYTES
    COLORIZE_MAX_FILE_BYTES = RenderedPathPreview.COLORIZE_MAX_FILE_BYTES
    PNG_SIGNATURE = RenderedPathPreview.PNG_SIGNATURE
    RenderedPath = RenderedPath
    SourcePaneClickResult = SourcePaneClickResult
    SourcePaneMouseHandlers = SourcePaneMouseHandlers
    SourcePaneGeometry = SourcePaneGeometry
    SourcePaneRenderer = SourcePaneRenderer
    _DIR_PREVIEW_CACHE = DirectoryPreview._DIR_PREVIEW_CACHE

    @staticmethod
    def colorize_source(source: str, target: Path, style: str) -> str:
        return RenderedPathPreview.colorize_source(source, target, style)

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
        return RenderedPathPreview.build_rendered_for_path(
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
        return DirectoryPreview.build_directory_preview(
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
        DirectoryPreview.clear_directory_preview_cache()

    @staticmethod
    def clear_diff_preview_cache() -> None:
        clear_diff_preview_cache()

    @staticmethod
    def copy_selected_source_range(
        state: AppState,
        start_pos: tuple[int, int],
        end_pos: tuple[int, int],
        copy_text_to_clipboard: Callable[[str], bool],
    ) -> bool:
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
        return _directory_preview_target_for_display_line(state, display_line)

    @staticmethod
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
        """Rebuild source-pane rendering for ``state.current_path``."""
        if force_rebuild:
            SourcePane.clear_directory_preview_cache()
            SourcePane.clear_diff_preview_cache()
        resolved_target = state.current_path.resolve()
        is_dir_target = resolved_target.is_dir()
        if is_dir_target:
            if reset_dir_budget or state.dir_preview_path != resolved_target:
                state.dir_preview_max_entries = SourcePane.initial_directory_preview_max_entries(
                    visible_content_rows()
                )
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
            dir_skip_gitignored=not state.show_hidden,
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

    @staticmethod
    def initial_directory_preview_max_entries(visible_rows: int) -> int:
        """Return initial preview budget bounded by viewport rows and global ceiling."""
        # Reserve rows for root/header plus the truncated footer marker.
        bounded_rows = max(1, visible_rows - 4)
        return min(SourcePane.DIR_PREVIEW_INITIAL_MAX_ENTRIES, bounded_rows)

    @staticmethod
    def directory_preview_growth_step(visible_rows: int) -> int:
        """Return adaptive growth step bounded by viewport size and global ceiling."""
        bounded_rows = max(1, visible_rows)
        target_step = max(16, bounded_rows * 2)
        return min(SourcePane.DIR_PREVIEW_GROWTH_STEP, target_step)

    @staticmethod
    def maybe_grow_directory_preview(
        state: AppState,
        visible_content_rows: Callable[[], int],
        refresh_rendered_for_current_path_fn: Callable[..., None],
    ) -> bool:
        """Increase directory-preview entry budget when scrolling near the end."""
        if state.dir_preview_path is None or not state.dir_preview_truncated:
            return False
        if state.current_path.resolve() != state.dir_preview_path:
            return False
        if state.dir_preview_max_entries >= SourcePane.DIR_PREVIEW_HARD_MAX_ENTRIES:
            return False

        visible_rows = max(1, visible_content_rows())
        near_end_threshold = max(1, visible_rows // 3)
        if state.start < max(0, state.max_start - near_end_threshold):
            return False

        previous_line_count = len(state.lines)
        growth_step = SourcePane.directory_preview_growth_step(visible_rows)
        min_target_entries = max(
            state.dir_preview_max_entries + growth_step,
            state.start + (visible_rows * 3),
        )
        next_max_entries = min(
            SourcePane.DIR_PREVIEW_HARD_MAX_ENTRIES,
            min_target_entries,
        )
        if next_max_entries <= state.dir_preview_max_entries:
            return False
        state.dir_preview_max_entries = next_max_entries
        refresh_rendered_for_current_path_fn(reset_scroll=False, reset_dir_budget=False)
        return len(state.lines) > previous_line_count

    @staticmethod
    def maybe_prefetch_directory_preview(
        state: AppState,
        visible_content_rows: Callable[[], int],
        refresh_rendered_for_current_path_fn: Callable[..., None],
        min_headroom_screens: int = 4,
        target_headroom_screens: int = 12,
    ) -> bool:
        """Grow directory preview during idle to keep scroll headroom ahead."""
        if state.dir_preview_path is None or not state.dir_preview_truncated:
            return False
        if state.current_path.resolve() != state.dir_preview_path:
            return False
        if state.dir_preview_max_entries >= SourcePane.DIR_PREVIEW_HARD_MAX_ENTRIES:
            return False

        visible_rows = max(1, visible_content_rows())
        min_headroom_lines = max(visible_rows, visible_rows * max(1, min_headroom_screens))
        headroom_lines = max(0, state.max_start - state.start)
        if headroom_lines >= min_headroom_lines:
            return False

        previous_line_count = len(state.lines)
        growth_step = SourcePane.directory_preview_growth_step(visible_rows)
        target_entries = state.start + (visible_rows * max(1, target_headroom_screens))
        next_max_entries = min(
            SourcePane.DIR_PREVIEW_HARD_MAX_ENTRIES,
            max(state.dir_preview_max_entries + growth_step, target_entries),
        )
        if next_max_entries <= state.dir_preview_max_entries:
            return False
        state.dir_preview_max_entries = next_max_entries
        refresh_rendered_for_current_path_fn(reset_scroll=False, reset_dir_budget=False)
        return len(state.lines) > previous_line_count

    @staticmethod
    def toggle_tree_size_labels(
        state: AppState,
        refresh_rendered_for_current_path_fn: Callable[..., None],
    ) -> None:
        """Toggle file-size labels in directory preview rendering."""
        state.show_tree_sizes = not state.show_tree_sizes
        if state.current_path.resolve().is_dir():
            refresh_rendered_for_current_path_fn(reset_scroll=False, reset_dir_budget=False)
        state.dirty = True

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
