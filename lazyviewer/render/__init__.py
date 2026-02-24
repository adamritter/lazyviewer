"""Frame renderer for lazyviewer terminal UI.

This module composes complete ANSI frames for both split-pane and text-only
layouts. It coordinates tree-pane and source-pane row renderers, help overlays,
and status bar text, then writes one atomic frame to stdout.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line
from ..source_pane.renderer import SourcePaneRenderer
from ..tree_pane.rendering import TreePaneRenderer
from .help import (
    help_panel_lines,
    help_panel_row_count,
    render_help_page,
)
from ..tree_model import TreeEntry, clamp_left_width


@dataclass
class RenderContext:
    """Input snapshot used to render one frame without further state mutation."""

    text_lines: list[str]
    text_start: int
    tree_entries: list[TreeEntry]
    tree_start: int
    tree_selected: int
    max_lines: int
    current_path: Path
    tree_root: Path
    expanded: set[Path]
    width: int
    left_width: int
    text_x: int
    wrap_text: bool
    browser_visible: bool
    show_hidden: bool
    show_help: bool = False
    show_tree_sizes: bool = True
    status_message: str = ""
    tree_filter_active: bool = False
    tree_filter_row_visible: bool = True
    tree_filter_mode: str = "files"
    tree_filter_query: str = ""
    tree_filter_editing: bool = False
    tree_filter_cursor_visible: bool = False
    tree_filter_match_count: int = 0
    tree_filter_truncated: bool = False
    tree_filter_loading: bool = False
    tree_filter_spinner_frame: int = 0
    tree_filter_prefix: str = "p>"
    tree_filter_placeholder: str = "type to filter files"
    picker_active: bool = False
    picker_mode: str = "symbols"
    picker_query: str = ""
    picker_items: list[str] | None = None
    picker_selected: int = 0
    picker_focus: str = "query"
    picker_list_start: int = 0
    picker_message: str = ""
    git_status_overlay: dict[Path, int] | None = None
    tree_search_query: str = ""
    text_search_query: str = ""
    text_search_current_line: int = 0
    text_search_current_column: int = 0
    preview_is_git_diff: bool = False
    source_selection_anchor: tuple[int, int] | None = None
    source_selection_focus: tuple[int, int] | None = None


def render_dual_page_context(context: RenderContext) -> None:
    """Render frame from a :class:`RenderContext` object."""
    render_dual_page(
        context.text_lines,
        context.text_start,
        context.tree_entries,
        context.tree_start,
        context.tree_selected,
        context.max_lines,
        context.current_path,
        context.tree_root,
        context.expanded,
        context.width,
        context.left_width,
        context.text_x,
        context.wrap_text,
        context.browser_visible,
        context.show_hidden,
        show_help=context.show_help,
        show_tree_sizes=context.show_tree_sizes,
        status_message=context.status_message,
        tree_filter_active=context.tree_filter_active,
        tree_filter_row_visible=context.tree_filter_row_visible,
        tree_filter_mode=context.tree_filter_mode,
        tree_filter_query=context.tree_filter_query,
        tree_filter_editing=context.tree_filter_editing,
        tree_filter_cursor_visible=context.tree_filter_cursor_visible,
        tree_filter_match_count=context.tree_filter_match_count,
        tree_filter_truncated=context.tree_filter_truncated,
        tree_filter_loading=context.tree_filter_loading,
        tree_filter_spinner_frame=context.tree_filter_spinner_frame,
        tree_filter_prefix=context.tree_filter_prefix,
        tree_filter_placeholder=context.tree_filter_placeholder,
        picker_active=context.picker_active,
        picker_mode=context.picker_mode,
        picker_query=context.picker_query,
        picker_items=context.picker_items,
        picker_selected=context.picker_selected,
        picker_focus=context.picker_focus,
        picker_list_start=context.picker_list_start,
        picker_message=context.picker_message,
        git_status_overlay=context.git_status_overlay,
        tree_search_query=context.tree_search_query,
        text_search_query=context.text_search_query,
        text_search_current_line=context.text_search_current_line,
        text_search_current_column=context.text_search_current_column,
        preview_is_git_diff=context.preview_is_git_diff,
        source_selection_anchor=context.source_selection_anchor,
        source_selection_focus=context.source_selection_focus,
    )


def _help_line(lines: tuple[str, ...], row: int) -> str:
    """Safe row lookup for help-line tuples."""
    if 0 <= row < len(lines):
        return lines[row]
    return ""


def build_status_line(left_text: str, width: int, right_text: str = "│ ? Help") -> str:
    """Build one-line status bar with right-aligned help hint."""
    usable = max(1, width)
    if usable <= len(right_text):
        return right_text[-usable:]
    left_limit = max(0, usable - len(right_text) - 1)
    left = left_text[:left_limit]
    gap = " " * (usable - len(left) - len(right_text))
    return f"{left}{gap}{right_text}"


def _compose_status_left_text(
    current_path: Path,
    status_start: int,
    status_end: int,
    status_total: int,
    text_percent: float,
    status_message: str,
) -> str:
    """Compose left status segment with location and transient message."""
    base = f"{current_path} ({status_start}-{status_end}/{status_total} {text_percent:5.1f}%)"
    if not status_message:
        return base
    return f"{base} · {status_message}"


def render_dual_page(
    text_lines: list[str],
    text_start: int,
    tree_entries: list[TreeEntry],
    tree_start: int,
    tree_selected: int,
    max_lines: int,
    current_path: Path,
    tree_root: Path,
    expanded: set[Path],
    width: int,
    left_width: int,
    text_x: int,
    wrap_text: bool,
    browser_visible: bool,
    show_hidden: bool,
    show_help: bool = False,
    show_tree_sizes: bool = True,
    status_message: str = "",
    tree_filter_active: bool = False,
    tree_filter_row_visible: bool = True,
    tree_filter_mode: str = "files",
    tree_filter_query: str = "",
    tree_filter_editing: bool = False,
    tree_filter_cursor_visible: bool = False,
    tree_filter_match_count: int = 0,
    tree_filter_truncated: bool = False,
    tree_filter_loading: bool = False,
    tree_filter_spinner_frame: int = 0,
    tree_filter_prefix: str = "p>",
    tree_filter_placeholder: str = "type to filter files",
    picker_active: bool = False,
    picker_mode: str = "symbols",
    picker_query: str = "",
    picker_items: list[str] | None = None,
    picker_selected: int = 0,
    picker_focus: str = "query",
    picker_list_start: int = 0,
    picker_message: str = "",
    git_status_overlay: dict[Path, int] | None = None,
    tree_search_query: str = "",
    text_search_query: str = "",
    text_search_current_line: int = 0,
    text_search_current_column: int = 0,
    preview_is_git_diff: bool = False,
    source_selection_anchor: tuple[int, int] | None = None,
    source_selection_focus: tuple[int, int] | None = None,
) -> None:
    """Render one full terminal frame for split or text-only mode."""
    out: list[str] = []
    out.append("\033[H\033[J")
    tree_help_lines, text_help_lines, text_only_help_lines = help_panel_lines(
        tree_filter_active=tree_filter_active,
        tree_filter_mode=tree_filter_mode,
        tree_filter_editing=tree_filter_editing,
    )
    help_rows = help_panel_row_count(
        max_lines,
        show_help,
        browser_visible=browser_visible,
        tree_filter_active=tree_filter_active,
        tree_filter_mode=tree_filter_mode,
        tree_filter_editing=tree_filter_editing,
    )
    content_rows = max(1, max_lines - help_rows)

    if not browser_visible:
        line_width = max(1, width)
        source_renderer = SourcePaneRenderer(
            text_lines,
            text_start,
            content_rows,
            line_width,
            current_path,
            wrap_text,
            text_x,
            text_search_query,
            text_search_current_line,
            text_search_current_column,
            preview_is_git_diff=preview_is_git_diff,
            source_selection_anchor=source_selection_anchor,
            source_selection_focus=source_selection_focus,
        )
        text_percent = source_renderer.text_percent
        status_start = source_renderer.status_start
        status_end = source_renderer.status_end
        status_total = source_renderer.status_total
        for row in range(content_rows):
            text_raw = source_renderer.render_row(row)
            out.append(text_raw)
            if "\033" in text_raw:
                out.append("\033[0m")
            out.append("\r\n")
        for row in range(help_rows):
            help_text = clip_ansi_line(_help_line(text_only_help_lines, row), line_width)
            out.append(help_text)
            if "\033" in help_text:
                out.append("\033[0m")
            out.append("\r\n")
        left_status = _compose_status_left_text(
            current_path,
            status_start,
            status_end,
            status_total,
            text_percent,
            status_message,
        )
        status = build_status_line(left_status, width)
        out.append("\033[7m")
        out.append(status)
        out.append("\033[0m")
        os.write(sys.stdout.fileno(), "".join(out).encode("utf-8", errors="replace"))
        return

    left_width = clamp_left_width(width, left_width)
    divider_width = 1
    right_width = max(1, width - left_width - divider_width)
    source_renderer = SourcePaneRenderer(
        text_lines,
        text_start,
        content_rows,
        right_width,
        current_path,
        wrap_text,
        text_x,
        text_search_query,
        text_search_current_line,
        text_search_current_column,
        preview_is_git_diff=preview_is_git_diff,
        source_selection_anchor=source_selection_anchor,
        source_selection_focus=source_selection_focus,
    )
    text_percent = source_renderer.text_percent
    status_start = source_renderer.status_start
    status_end = source_renderer.status_end
    status_total = source_renderer.status_total
    tree_renderer = TreePaneRenderer(
        left_width=left_width,
        content_rows=content_rows,
        tree_entries=tree_entries,
        tree_start=tree_start,
        tree_selected=tree_selected,
        tree_root=tree_root,
        expanded=expanded,
        show_tree_sizes=show_tree_sizes,
        git_status_overlay=git_status_overlay,
        tree_search_query=tree_search_query,
        tree_filter_active=tree_filter_active,
        tree_filter_row_visible=tree_filter_row_visible,
        tree_filter_query=tree_filter_query,
        tree_filter_editing=tree_filter_editing,
        tree_filter_cursor_visible=tree_filter_cursor_visible,
        tree_filter_match_count=tree_filter_match_count,
        tree_filter_truncated=tree_filter_truncated,
        tree_filter_loading=tree_filter_loading,
        tree_filter_spinner_frame=tree_filter_spinner_frame,
        tree_filter_prefix=tree_filter_prefix,
        tree_filter_placeholder=tree_filter_placeholder,
        picker_active=picker_active,
        picker_mode=picker_mode,
        picker_query=picker_query,
        picker_items=picker_items,
        picker_selected=picker_selected,
        picker_focus=picker_focus,
        picker_list_start=picker_list_start,
        picker_message=picker_message,
    )

    for row in range(content_rows):
        out.append(tree_renderer.padded_row_text(row))

        out.append("\033[2m│\033[0m")

        text_raw = source_renderer.render_row(row)
        out.append(text_raw)
        if "\033" in text_raw:
            out.append("\033[0m")
        out.append("\r\n")

    for row in range(help_rows):
        left_help = clip_ansi_line(_help_line(tree_help_lines, row), left_width)
        out.append(left_help)
        left_plain = ANSI_ESCAPE_RE.sub("", left_help)
        left_len = sum(char_display_width(ch, 0) for ch in left_plain)
        if left_len < left_width:
            out.append(" " * (left_width - left_len))
        out.append("\033[2m│\033[0m")

        right_help = clip_ansi_line(_help_line(text_help_lines, row), right_width)
        out.append(right_help)
        if "\033" in right_help:
            out.append("\033[0m")
        out.append("\r\n")

    left_status = _compose_status_left_text(
        current_path,
        status_start,
        status_end,
        status_total,
        text_percent,
        status_message,
    )
    status = build_status_line(left_status, width)
    out.append("\033[7m")
    out.append(status)
    out.append("\033[0m")

    os.write(sys.stdout.fileno(), "".join(out).encode("utf-8", errors="replace"))
