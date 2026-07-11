"""Layout policy and geometry helpers for the interactive runtime.

This module keeps pane-size and reflow decisions centralized so the event loop
and key handlers can ask for layout effects without duplicating math. It owns
the mode-specific left-pane persistence behavior (normal vs content-search),
derives visible content rows after optional help UI, and exposes preview-image
placement data used by kitty graphics rendering.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ..session import SessionState
from ..ports import BuildScreenLines, HelpPanelRowCount


class PagerLayout:
    """Layout/state helpers for pane sizing and preview geometry."""

    def __init__(
        self,
        state: SessionState,
        kitty_graphics_supported: bool,
        *,
        help_panel_row_count: HelpPanelRowCount,
        build_screen_lines: BuildScreenLines,
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
        """Bind layout operations to app state and persistence callbacks."""
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
        """Return preview-pane text width for current browser visibility."""
        if columns is None:
            columns = self._get_terminal_size((80, 24)).columns
        if self.state.layout.browser_visible:
            return max(1, columns - self.state.layout.left_width - 1)
        return max(1, columns)

    def visible_content_rows(self) -> int:
        """Return number of content rows after reserving help panel rows."""
        help_rows = self._help_panel_row_count(
            self.state.layout.usable_rows,
            self.state.layout.show_help,
            browser_visible=self.state.layout.browser_visible,
            tree_filter_active=self.state.filter.active,
            tree_filter_mode=self.state.filter.mode,
            tree_filter_editing=self.state.filter.editing,
        )
        return max(1, self.state.layout.usable_rows - help_rows)

    def content_search_match_view_active(self) -> bool:
        """Return whether content-search results are currently being shown."""
        return (
            self.state.filter.active
            and self.state.filter.mode == "content"
            and bool(self.state.filter.query)
        )

    def rebuild_screen_lines(
        self,
        columns: int | None = None,
        preserve_scroll: bool = True,
    ) -> None:
        """Reflow rendered text into screen lines and clamp scroll offsets."""
        self.state.preview.lines = self._build_screen_lines(
            self.state.preview.rendered,
            self.effective_text_width(columns),
            wrap=self.state.preview.wrap,
        )
        self.state.preview.max_scroll = max(0, len(self.state.preview.lines) - self.visible_content_rows())
        if preserve_scroll:
            self.state.preview.scroll = max(0, min(self.state.preview.scroll, self.state.preview.max_scroll))
        else:
            self.state.preview.scroll = 0
        if self.state.preview.wrap:
            self.state.preview.horizontal_scroll = 0

    def sync_left_width_for_tree_filter_mode(self, force: bool = False) -> None:
        """Switch left-pane width profile when content-search view toggles.

        Content-search mode can use its own persisted pane width percentage.
        """
        use_content_mode_width = self.content_search_match_view_active()
        if not force and use_content_mode_width == self.content_mode_left_width_active:
            return
        self.content_mode_left_width_active = use_content_mode_width

        columns = self._get_terminal_size((80, 24)).columns
        if use_content_mode_width:
            saved_percent = self._load_content_search_left_pane_percent()
            if saved_percent is None:
                current_percent = (self.state.layout.left_width / max(1, columns)) * 100.0
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
        if desired_left == self.state.layout.left_width:
            return

        self.state.layout.left_width = desired_left
        self.state.layout.right_width = max(1, columns - self.state.layout.left_width - 1)
        if self.state.layout.right_width != self.state.layout.last_right_width:
            self.state.layout.last_right_width = self.state.layout.right_width
            self.rebuild_screen_lines(columns=columns)
        self.state.interface.dirty = True

    def save_left_pane_width_for_mode(self, total_width: int, left_width: int) -> None:
        """Persist pane width in mode-specific config slot."""
        if self.content_search_match_view_active():
            self._save_content_search_left_pane_percent(total_width, left_width)
            return
        self._save_left_pane_percent(total_width, left_width)

    def show_inline_error(self, message: str) -> None:
        """Replace preview with an inline red error message and reset media state."""
        self.state.preview.rendered = f"\033[31m{message}\033[0m"
        self.rebuild_screen_lines(preserve_scroll=False)
        self.state.preview.horizontal_scroll = 0
        self.state.preview.directory_path = None
        self.state.preview.directory_truncated = False
        self.state.preview.image_path = None
        self.state.preview.image_format = None
        self.state.interface.dirty = True

    def current_preview_image_path(self) -> Path | None:
        """Return resolved PNG path eligible for kitty graphics rendering."""
        if not self.kitty_graphics_supported:
            return None
        if self.state.preview.image_format != "png":
            return None
        if self.state.preview.image_path is None:
            return None
        try:
            image_path = self.state.preview.image_path.resolve()
        except Exception:
            image_path = self.state.preview.image_path
        if not image_path.exists() or not image_path.is_file():
            return None
        return image_path

    def current_preview_image_geometry(self, columns: int) -> tuple[int, int, int, int]:
        """Return kitty image placement as ``(col, row, width_cells, height_cells)``."""
        image_rows = self.visible_content_rows()
        if self.state.layout.browser_visible:
            image_col = self.state.layout.left_width + 2
            image_width = max(1, columns - self.state.layout.left_width - 1)
        else:
            image_col = 1
            image_width = max(1, columns)
        return image_col, 1, image_width, image_rows
