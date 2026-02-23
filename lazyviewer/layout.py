"""Layout operations for app runtime."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from .state import AppState


class PagerLayoutOps:
    def __init__(
        self,
        state: AppState,
        kitty_graphics_supported: bool,
        *,
        help_panel_row_count: Callable[..., int],
        build_screen_lines: Callable[..., list[str]],
        get_terminal_size: Callable[[tuple[int, int]], os.terminal_size],
        load_content_search_left_pane_percent: Callable[[], float | None],
        load_left_pane_percent: Callable[[], float | None],
        save_content_search_left_pane_percent: Callable[[int, int], None],
        save_left_pane_percent: Callable[[int, int], None],
        compute_left_width: Callable[[int], int],
        clamp_left_width: Callable[[int, int], int],
        content_search_left_pane_min_percent: float,
        content_search_left_pane_fallback_delta_percent: float,
    ) -> None:
        self.state = state
        self.kitty_graphics_supported = kitty_graphics_supported
        self._help_panel_row_count = help_panel_row_count
        self._build_screen_lines = build_screen_lines
        self._get_terminal_size = get_terminal_size
        self._load_content_search_left_pane_percent = load_content_search_left_pane_percent
        self._load_left_pane_percent = load_left_pane_percent
        self._save_content_search_left_pane_percent = save_content_search_left_pane_percent
        self._save_left_pane_percent = save_left_pane_percent
        self._compute_left_width = compute_left_width
        self._clamp_left_width = clamp_left_width
        self._content_search_left_pane_min_percent = content_search_left_pane_min_percent
        self._content_search_left_pane_fallback_delta_percent = content_search_left_pane_fallback_delta_percent
        self.content_mode_left_width_active = self.content_search_match_view_active()

    def effective_text_width(self, columns: int | None = None) -> int:
        if columns is None:
            columns = self._get_terminal_size((80, 24)).columns
        if self.state.browser_visible:
            return max(1, columns - self.state.left_width - 2)
        return max(1, columns - 1)

    def visible_content_rows(self) -> int:
        help_rows = self._help_panel_row_count(
            self.state.usable,
            self.state.show_help,
            browser_visible=self.state.browser_visible,
            tree_filter_active=self.state.tree_filter_active,
            tree_filter_mode=self.state.tree_filter_mode,
            tree_filter_editing=self.state.tree_filter_editing,
        )
        return max(1, self.state.usable - help_rows)

    def content_search_match_view_active(self) -> bool:
        return (
            self.state.tree_filter_active
            and self.state.tree_filter_mode == "content"
            and bool(self.state.tree_filter_query)
        )

    def rebuild_screen_lines(
        self,
        columns: int | None = None,
        preserve_scroll: bool = True,
    ) -> None:
        self.state.lines = self._build_screen_lines(
            self.state.rendered,
            self.effective_text_width(columns),
            wrap=self.state.wrap_text,
        )
        self.state.max_start = max(0, len(self.state.lines) - self.visible_content_rows())
        if preserve_scroll:
            self.state.start = max(0, min(self.state.start, self.state.max_start))
        else:
            self.state.start = 0
        if self.state.wrap_text:
            self.state.text_x = 0

    def sync_left_width_for_tree_filter_mode(self, force: bool = False) -> None:
        use_content_mode_width = self.content_search_match_view_active()
        if not force and use_content_mode_width == self.content_mode_left_width_active:
            return
        self.content_mode_left_width_active = use_content_mode_width

        columns = self._get_terminal_size((80, 24)).columns
        if use_content_mode_width:
            saved_percent = self._load_content_search_left_pane_percent()
            if saved_percent is None:
                current_percent = (self.state.left_width / max(1, columns)) * 100.0
                saved_percent = min(
                    99.0,
                    max(
                        self._content_search_left_pane_min_percent,
                        current_percent + self._content_search_left_pane_fallback_delta_percent,
                    ),
                )
        else:
            saved_percent = self._load_left_pane_percent()

        if saved_percent is None:
            desired_left = self._compute_left_width(columns)
        else:
            desired_left = int((saved_percent / 100.0) * columns)
        desired_left = self._clamp_left_width(columns, desired_left)
        if desired_left == self.state.left_width:
            return

        self.state.left_width = desired_left
        self.state.right_width = max(1, columns - self.state.left_width - 2)
        if self.state.right_width != self.state.last_right_width:
            self.state.last_right_width = self.state.right_width
            self.rebuild_screen_lines(columns=columns)
        self.state.dirty = True

    def save_left_pane_width_for_mode(self, total_width: int, left_width: int) -> None:
        if self.content_search_match_view_active():
            self._save_content_search_left_pane_percent(total_width, left_width)
            return
        self._save_left_pane_percent(total_width, left_width)

    def show_inline_error(self, message: str) -> None:
        self.state.rendered = f"\033[31m{message}\033[0m"
        self.rebuild_screen_lines(preserve_scroll=False)
        self.state.text_x = 0
        self.state.dir_preview_path = None
        self.state.dir_preview_truncated = False
        self.state.preview_image_path = None
        self.state.preview_image_format = None
        self.state.dirty = True

    def current_preview_image_path(self) -> Path | None:
        if not self.kitty_graphics_supported:
            return None
        if self.state.preview_image_format != "png":
            return None
        if self.state.preview_image_path is None:
            return None
        try:
            image_path = self.state.preview_image_path.resolve()
        except Exception:
            image_path = self.state.preview_image_path
        if not image_path.exists() or not image_path.is_file():
            return None
        return image_path

    def current_preview_image_geometry(self, columns: int) -> tuple[int, int, int, int]:
        image_rows = self.visible_content_rows()
        if self.state.browser_visible:
            image_col = self.state.left_width + 2
            image_width = max(1, columns - self.state.left_width - 2 - 1)
        else:
            image_col = 1
            image_width = max(1, columns - 1)
        return image_col, 1, image_width, image_rows

