"""Tree-pane rendering helpers for the split tree/preview layout."""

from __future__ import annotations

from pathlib import Path

from ..ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line
from .model import TreeEntry, format_tree_entry

FILTER_SPINNER_FRAMES: tuple[str, ...] = ("|", "/", "-", "\\")


def selected_with_ansi(text: str) -> str:
    """Apply selection styling without discarding existing ANSI colors."""
    if not text:
        return text

    # Keep reverse video active even when the text contains internal resets.
    return "\033[7m" + text.replace("\033[0m", "\033[0;7m") + "\033[0m"


def _display_width(text: str) -> int:
    """Return terminal column width for ANSI-styled text."""
    plain = ANSI_ESCAPE_RE.sub("", text)
    col = 0
    for ch in plain:
        col += char_display_width(ch, col)
    return col


def _format_tree_filter_status(
    query: str,
    match_count: int,
    truncated: bool,
    loading: bool,
    spinner_frame: int,
) -> str:
    """Build the right-side status fragment shown on the filter query row.

    The label is omitted when query text is empty. While loading, a spinner is
    shown; once loading settles, zero matches become ``no results``.
    """
    if not query:
        return ""

    parts: list[str] = []
    if loading:
        spinner = FILTER_SPINNER_FRAMES[spinner_frame % len(FILTER_SPINNER_FRAMES)]
        parts.append(f"{spinner} searching")

    if match_count <= 0:
        if not loading:
            parts.append("no results")
    else:
        noun = "match" if match_count == 1 else "matches"
        parts.append(f"{match_count:,} {noun}")

    if truncated:
        parts.append("truncated")

    return " · ".join(parts)


class TreePaneRenderer:
    """Render left-pane rows for tree, filter-query, and picker overlay states.

    The renderer receives a snapshot of pane state at construction time and
    precomputes derived flags (such as whether the picker overlay suppresses the
    filter row) so each row can be rendered deterministically.
    """

    def __init__(
        self,
        left_width: int,
        content_rows: int,
        tree_entries: list[TreeEntry],
        tree_start: int,
        tree_selected: int,
        tree_root: Path,
        expanded: set[Path],
        show_tree_sizes: bool,
        git_status_overlay: dict[Path, int] | None,
        tree_search_query: str,
        tree_filter_active: bool,
        tree_filter_query: str,
        tree_filter_editing: bool,
        tree_filter_cursor_visible: bool,
        tree_filter_match_count: int,
        tree_filter_truncated: bool,
        tree_filter_loading: bool,
        tree_filter_spinner_frame: int,
        tree_filter_prefix: str,
        tree_filter_placeholder: str,
        picker_active: bool,
        picker_mode: str,
        picker_query: str,
        picker_items: list[str] | None,
        picker_selected: int,
        picker_focus: str,
        picker_list_start: int,
        picker_message: str,
    ) -> None:
        """Normalize pane state used by row renderers.

        The initializer clamps picker indices into valid bounds and computes row
        offsets so later rendering does not need to repeat that bookkeeping.
        """
        self.left_width = left_width
        self.tree_entries = tree_entries
        self.tree_start = tree_start
        self.tree_selected = tree_selected
        self.tree_root = tree_root
        self.expanded = expanded
        self.show_tree_sizes = show_tree_sizes
        self.git_status_overlay = git_status_overlay
        self.tree_search_query = tree_search_query
        self.tree_filter_active = tree_filter_active
        self.tree_filter_query = tree_filter_query
        self.tree_filter_editing = tree_filter_editing
        self.tree_filter_cursor_visible = tree_filter_cursor_visible
        self.tree_filter_match_count = tree_filter_match_count
        self.tree_filter_truncated = tree_filter_truncated
        self.tree_filter_loading = tree_filter_loading
        self.tree_filter_spinner_frame = tree_filter_spinner_frame
        self.tree_filter_prefix = tree_filter_prefix
        self.tree_filter_placeholder = tree_filter_placeholder
        self.picker_mode = picker_mode
        self.picker_query = picker_query
        self.picker_focus = picker_focus
        self.picker_message = picker_message

        self.picker_overlay_active = picker_active and picker_mode in {"symbols", "commands"}
        self.tree_filter_row_visible = tree_filter_active and not self.picker_overlay_active
        self.tree_row_offset = 1 if self.tree_filter_row_visible else 0

        self.picker_items = picker_items if self.picker_overlay_active and picker_items else []
        if self.picker_items:
            self.picker_selected = max(0, min(picker_selected, len(self.picker_items) - 1))
        else:
            self.picker_selected = 0
        picker_rows = max(1, content_rows - 1)
        max_picker_start = max(0, len(self.picker_items) - picker_rows)
        self.picker_list_start = max(0, min(picker_list_start, max_picker_start))

    def render_row(self, row: int) -> str:
        """Render one logical row without right-padding."""
        if self.picker_overlay_active:
            return self._render_picker_row(row)
        if self.tree_filter_row_visible and row == 0:
            return self._render_filter_row()
        return self._render_tree_row(row)

    def padded_row_text(self, row: int) -> str:
        """Render one row and pad it to ``left_width`` display columns."""
        tree_text = self.render_row(row)
        tree_len = _display_width(tree_text)
        if tree_len < self.left_width:
            return tree_text + (" " * (self.left_width - tree_len))
        return tree_text

    def _render_picker_row(self, row: int) -> str:
        """Render the command/symbol picker overlay row."""
        if self.picker_mode == "commands":
            query_prefix = ": "
            placeholder = "type to filter commands"
        else:
            query_prefix = "s> "
            placeholder = "type to filter symbols"

        if row == 0:
            if self.picker_query:
                query_text = f"\033[1;38;5;81m{query_prefix}{self.picker_query}\033[0m"
            else:
                query_text = f"\033[2;38;5;250m{query_prefix}{placeholder}\033[0m"
            tree_text = clip_ansi_line(query_text, self.left_width)
            if self.picker_focus == "query":
                return selected_with_ansi(tree_text)
            return tree_text

        picker_idx = self.picker_list_start + row - 1
        if picker_idx < len(self.picker_items):
            tree_text = clip_ansi_line(f" {self.picker_items[picker_idx]}", self.left_width)
            if picker_idx == self.picker_selected:
                if self.picker_focus == "tree":
                    return selected_with_ansi(tree_text)
                return f"\033[38;5;81m{tree_text}\033[0m"
            return tree_text

        if row == 1 and self.picker_message:
            return clip_ansi_line(f"\033[2;38;5;250m{self.picker_message}\033[0m", self.left_width)
        return ""

    def _render_filter_row(self) -> str:
        """Render the tree-filter query row, including cursor and match status."""
        status_label = _format_tree_filter_status(
            self.tree_filter_query,
            self.tree_filter_match_count,
            self.tree_filter_truncated,
            self.tree_filter_loading,
            self.tree_filter_spinner_frame,
        )
        if self.tree_filter_editing:
            base = (
                f"{self.tree_filter_prefix} {self.tree_filter_query}"
                if self.tree_filter_query
                else f"{self.tree_filter_prefix} "
            )
            cursor = "_" if self.tree_filter_cursor_visible else " "
            query_text = f"\033[1;38;5;81m{base}{cursor}\033[0m"
        elif self.tree_filter_query:
            query_text = f"\033[1;38;5;81m{self.tree_filter_prefix} {self.tree_filter_query}\033[0m"
        else:
            query_text = f"\033[2;38;5;250m{self.tree_filter_prefix} {self.tree_filter_placeholder}\033[0m"

        if status_label:
            query_text += f"\033[2;38;5;250m  {status_label}\033[0m"

        tree_text = clip_ansi_line(query_text, self.left_width)
        if self.tree_filter_editing:
            return selected_with_ansi(tree_text)
        return tree_text

    def _render_tree_row(self, row: int) -> str:
        """Render a regular tree entry row, applying selection highlighting."""
        tree_idx = self.tree_start + row - self.tree_row_offset
        if tree_idx >= len(self.tree_entries):
            return ""

        tree_text = format_tree_entry(
            self.tree_entries[tree_idx],
            self.tree_root,
            self.expanded,
            git_status_overlay=self.git_status_overlay,
            search_query=self.tree_search_query,
            show_size_labels=self.show_tree_sizes,
        )
        tree_text = clip_ansi_line(tree_text, self.left_width)
        if tree_idx == self.tree_selected:
            return selected_with_ansi(tree_text)
        return tree_text
