"""Rendering engine for the split tree/text terminal view.

Defines render context data and writes fully composed ANSI frames.
Also computes status/help/search overlays without mutating runtime state.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from ..ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line
from ..preview import rendering as preview_rendering
from .help import (
    help_panel_lines,
    help_panel_row_count,
    render_help_page,
)
from ..tree import TreeEntry, clamp_left_width, format_tree_entry

FILTER_SPINNER_FRAMES: tuple[str, ...] = ("|", "/", "-", "\\")


@dataclass
class RenderContext:
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
    status_message: str = ""
    tree_filter_active: bool = False
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
        status_message=context.status_message,
        tree_filter_active=context.tree_filter_active,
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


def selected_with_ansi(text: str) -> str:
    """Apply selection styling without discarding existing ANSI colors."""
    if not text:
        return text

    # Keep reverse video active even when the text contains internal resets.
    return "\033[7m" + text.replace("\033[0m", "\033[0;7m") + "\033[0m"


def _help_line(lines: tuple[str, ...], row: int) -> str:
    if 0 <= row < len(lines):
        return lines[row]
    return ""


def build_status_line(left_text: str, width: int, right_text: str = "│ ? Help") -> str:
    usable = max(1, width - 1)
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
    base = f"{current_path} ({status_start}-{status_end}/{status_total} {text_percent:5.1f}%)"
    if not status_message:
        return base
    return f"{base} · {status_message}"


# Compatibility aliases for tests and external imports.
_source_line_raw_text = preview_rendering.source_line_raw_text
sticky_symbol_headers_for_position = preview_rendering.sticky_symbol_headers_for_position


def _format_tree_filter_status(
    query: str,
    match_count: int,
    truncated: bool,
    loading: bool,
    spinner_frame: int,
) -> str:
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
    status_message: str = "",
    tree_filter_active: bool = False,
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
    has_current_text_hit = text_search_current_line > 0 and text_search_current_column > 0
    selection_range = preview_rendering.normalized_selection_range(source_selection_anchor, source_selection_focus)

    sticky_symbols = sticky_symbol_headers_for_position(
        text_lines=text_lines,
        text_start=text_start,
        content_rows=content_rows,
        current_path=current_path,
        wrap_text=wrap_text,
        preview_is_git_diff=preview_is_git_diff,
    )

    if not browser_visible:
        line_width = max(1, width - 1)
        sticky_headers = preview_rendering.formatted_sticky_headers(
            text_lines,
            sticky_symbols,
            line_width,
            wrap_text,
            text_x,
            preview_is_git_diff=preview_is_git_diff,
        )
        sticky_header_rows = len(sticky_headers)
        text_content_rows = max(1, content_rows - sticky_header_rows)
        text_percent = preview_rendering.scroll_percent(text_start, len(text_lines), text_content_rows)
        status_start, status_end, status_total = preview_rendering.status_line_range(
            text_lines,
            text_start,
            text_content_rows,
            wrap_text,
        )
        for row in range(content_rows):
            if row < sticky_header_rows:
                sticky_text = sticky_headers[row]
                out.append(sticky_text)
                if "\033" in sticky_text:
                    out.append("\033[0m")
                out.append("\r\n")
                continue

            text_idx = text_start + row
            if text_idx < len(text_lines):
                text_raw = preview_rendering.rendered_preview_row(
                    text_lines,
                    text_idx,
                    line_width,
                    wrap_text,
                    text_x,
                    text_search_query,
                    text_search_current_line,
                    text_search_current_column,
                    has_current_text_hit,
                    selection_range,
                )
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
    right_width = max(1, width - left_width - divider_width - 1)
    sticky_headers = preview_rendering.formatted_sticky_headers(
        text_lines,
        sticky_symbols,
        right_width,
        wrap_text,
        text_x,
        preview_is_git_diff=preview_is_git_diff,
    )
    sticky_header_rows = len(sticky_headers)
    text_content_rows = max(1, content_rows - sticky_header_rows)

    text_percent = preview_rendering.scroll_percent(text_start, len(text_lines), text_content_rows)
    status_start, status_end, status_total = preview_rendering.status_line_range(
        text_lines,
        text_start,
        text_content_rows,
        wrap_text,
    )
    picker_overlay_active = picker_active and picker_mode in {"symbols", "commands"}
    tree_filter_row_visible = tree_filter_active and not picker_overlay_active
    tree_row_offset = 1 if tree_filter_row_visible else 0
    items = picker_items if picker_overlay_active else []
    if items:
        picker_selected = max(0, min(picker_selected, len(items) - 1))
    else:
        picker_selected = 0
    picker_rows = max(1, content_rows - 1)
    max_picker_start = max(0, len(items) - picker_rows)
    picker_list_start = max(0, min(picker_list_start, max_picker_start))

    for row in range(content_rows):
        if picker_overlay_active:
            if picker_mode == "commands":
                query_prefix = ": "
                placeholder = "type to filter commands"
            else:
                query_prefix = "s> "
                placeholder = "type to filter symbols"
            if row == 0:
                if picker_query:
                    query_text = f"\033[1;38;5;81m{query_prefix}{picker_query}\033[0m"
                else:
                    query_text = f"\033[2;38;5;250m{query_prefix}{placeholder}\033[0m"
                tree_text = clip_ansi_line(query_text, left_width)
                if picker_focus == "query":
                    tree_text = selected_with_ansi(tree_text)
            else:
                picker_idx = picker_list_start + row - 1
                if picker_idx < len(items):
                    tree_text = clip_ansi_line(f" {items[picker_idx]}", left_width)
                    if picker_idx == picker_selected:
                        if picker_focus == "tree":
                            tree_text = selected_with_ansi(tree_text)
                        else:
                            tree_text = f"\033[38;5;81m{tree_text}\033[0m"
                elif row == 1 and picker_message:
                    tree_text = clip_ansi_line(f"\033[2;38;5;250m{picker_message}\033[0m", left_width)
                else:
                    tree_text = ""
        elif tree_filter_row_visible and row == 0:
            status_label = _format_tree_filter_status(
                tree_filter_query,
                tree_filter_match_count,
                tree_filter_truncated,
                tree_filter_loading,
                tree_filter_spinner_frame,
            )
            if tree_filter_editing:
                base = f"{tree_filter_prefix} {tree_filter_query}" if tree_filter_query else f"{tree_filter_prefix} "
                cursor = "_" if tree_filter_cursor_visible else " "
                query_text = f"\033[1;38;5;81m{base}{cursor}\033[0m"
            elif tree_filter_query:
                query_text = f"\033[1;38;5;81m{tree_filter_prefix} {tree_filter_query}\033[0m"
            else:
                query_text = f"\033[2;38;5;250m{tree_filter_prefix} {tree_filter_placeholder}\033[0m"
            if status_label:
                query_text += f"\033[2;38;5;250m  {status_label}\033[0m"
            tree_text = clip_ansi_line(query_text, left_width)
            if tree_filter_editing:
                tree_text = selected_with_ansi(tree_text)
        else:
            tree_idx = tree_start + row - tree_row_offset
            if tree_idx < len(tree_entries):
                tree_text = format_tree_entry(
                    tree_entries[tree_idx],
                    tree_root,
                    expanded,
                    git_status_overlay=git_status_overlay,
                    search_query=tree_search_query,
                )
                tree_text = clip_ansi_line(tree_text, left_width)
                if tree_idx == tree_selected:
                    tree_text = selected_with_ansi(tree_text)
            else:
                tree_text = ""
        out.append(tree_text)
        tree_plain = ANSI_ESCAPE_RE.sub("", tree_text)
        tree_len = sum(char_display_width(ch, 0) for ch in tree_plain)
        if tree_len < left_width:
            out.append(" " * (left_width - tree_len))

        out.append("\033[2m│\033[0m")

        if row < sticky_header_rows:
            sticky_text = sticky_headers[row]
            out.append(sticky_text)
            if "\033" in sticky_text:
                out.append("\033[0m")
        else:
            text_idx = text_start + row
            if text_idx < len(text_lines):
                text_raw = preview_rendering.rendered_preview_row(
                    text_lines,
                    text_idx,
                    right_width,
                    wrap_text,
                    text_x,
                    text_search_query,
                    text_search_current_line,
                    text_search_current_column,
                    has_current_text_hit,
                    selection_range,
                )
                out.append(text_raw)
                if "\033" in text_raw:
                    out.append("\033[0m")
            else:
                out.append("")
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
