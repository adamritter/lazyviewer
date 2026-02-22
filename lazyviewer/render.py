from __future__ import annotations

import os
import sys
from pathlib import Path

from .ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line, slice_ansi_line
from .tree import TreeEntry, clamp_left_width, format_tree_entry

FILTER_SPINNER_FRAMES: tuple[str, ...] = ("|", "/", "-", "\\")

HELP_PANEL_TREE_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mTREE\033[0m",
    "\033[2;38;5;250mnav:\033[0m \033[38;5;229mh/j/k/l\033[0m move  \033[38;5;229mEnter\033[0m toggle/open",
    "\033[2;38;5;250mfilter:\033[0m \033[38;5;229mCtrl+P\033[0m files  \033[38;5;229m/\033[0m content  \033[38;5;229mEnter\033[0m keep  \033[38;5;229mTab\033[0m edit  \033[38;5;229mn/N\033[0m hits",
    "\033[2;38;5;250mroot/nav:\033[0m \033[38;5;229mr\033[0m set root  \033[38;5;229mR\033[0m parent root  \033[38;5;229mAlt+Left/Right\033[0m back/forward",
    "\033[2;38;5;250mmarks:\033[0m \033[38;5;229mm{key}\033[0m set  \033[38;5;229m'{key}\033[0m jump  \033[38;5;229mCtrl+U/D\033[0m smart dir jump (max 10)",
)

HELP_PANEL_TEXT_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mTEXT + EXTRAS\033[0m",
    "\033[2;38;5;250mscroll:\033[0m \033[38;5;229mUp/Down\033[0m line  \033[38;5;229md/u\033[0m half  \033[38;5;229mf/B\033[0m page  \033[38;5;229mg/G/10G\033[0m",
    "\033[2;38;5;250medit:\033[0m \033[38;5;229mLeft/Right\033[0m x-scroll  \033[38;5;229mw\033[0m wrap  \033[38;5;229me\033[0m edit",
    "\033[2;38;5;250mextra:\033[0m \033[38;5;229m:\033[0m commands  \033[38;5;229ms\033[0m symbols  \033[38;5;229mm{key}/'{key}\033[0m marks  \033[38;5;229mt\033[0m tree",
    "\033[2;38;5;250mmeta:\033[0m \033[38;5;229m.\033[0m hidden  \033[38;5;229mAlt+Left/Right\033[0m history  \033[38;5;229m?\033[0m help  \033[38;5;229mq\033[0m quit",
)

HELP_PANEL_TEXT_ONLY_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mKEYS\033[0m",
    "\033[2;38;5;250mscroll:\033[0m \033[38;5;229mUp/Down\033[0m  \033[38;5;229md/u\033[0m  \033[38;5;229mf/B\033[0m  \033[38;5;229mg/G/10G\033[0m  \033[38;5;229mLeft/Right\033[0m",
    "\033[2;38;5;250medit:\033[0m \033[38;5;229mw\033[0m wrap  \033[38;5;229me\033[0m edit  \033[38;5;229ms\033[0m symbols  \033[38;5;229mt\033[0m tree  \033[38;5;229mr/R\033[0m root  \033[38;5;229m.\033[0m hidden",
    "\033[2;38;5;250mmeta:\033[0m \033[38;5;229m:\033[0m commands  \033[38;5;229mCtrl+P\033[0m files  \033[38;5;229m/\033[0m content  \033[38;5;229mn/N\033[0m hits",
    "\033[2;38;5;250mnav:\033[0m \033[38;5;229mm{key}/'{key}\033[0m marks  \033[38;5;229mAlt+Left/Right\033[0m history  \033[38;5;229m?\033[0m help  \033[38;5;229mq\033[0m quit",
)


def selected_with_ansi(text: str) -> str:
    """Apply selection styling without discarding existing ANSI colors."""
    if not text:
        return text

    # Keep reverse video active even when the text contains internal resets.
    return "\033[7m" + text.replace("\033[0m", "\033[0;7m") + "\033[0m"


def help_panel_row_count(max_lines: int, show_help: bool) -> int:
    if not show_help:
        return 0
    if max_lines <= 1:
        return 0
    required_rows = max(
        len(HELP_PANEL_TREE_LINES),
        len(HELP_PANEL_TEXT_LINES),
        len(HELP_PANEL_TEXT_ONLY_LINES),
    )
    return min(required_rows, max_lines - 1)


def build_status_line(left_text: str, width: int, right_text: str = "│ ? Help") -> str:
    usable = max(1, width - 1)
    if usable <= len(right_text):
        return right_text[-usable:]
    left_limit = max(0, usable - len(right_text) - 1)
    left = left_text[:left_limit]
    gap = " " * (usable - len(left) - len(right_text))
    return f"{left}{gap}{right_text}"


def _scroll_percent(text_start: int, total_lines: int, visible_rows: int) -> float:
    if total_lines <= 0:
        return 0.0
    max_start = max(0, total_lines - max(1, visible_rows))
    if max_start <= 0:
        return 0.0
    clamped_start = max(0, min(text_start, max_start))
    return (clamped_start / max_start) * 100.0


def _highlight_ansi_substrings(
    text: str,
    query: str,
    current_column: int | None = None,
    has_current_target: bool = False,
) -> str:
    if not text or not query:
        return text

    visible_chars: list[str] = []
    visible_start: list[int] = []
    visible_end: list[int] = []

    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                i = match.end()
                continue
        visible_start.append(i)
        visible_chars.append(text[i])
        i += 1
        visible_end.append(i)

    if not visible_chars:
        return text

    visible_text = "".join(visible_chars)
    folded_text = visible_text.casefold()
    folded_query = query.casefold()
    if not folded_query:
        return text

    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        idx = folded_text.find(folded_query, cursor)
        if idx < 0:
            break
        end = idx + len(query)
        spans.append((idx, end))
        cursor = end

    if not spans:
        return text

    current_idx: int | None = None
    if has_current_target and current_column is not None and spans:
        target = max(0, current_column - 1)
        for idx, (start_vis, end_vis) in enumerate(spans):
            if start_vis <= target < end_vis:
                current_idx = idx
                break
        if current_idx is None:
            for idx, (start_vis, _end_vis) in enumerate(spans):
                if start_vis >= target:
                    current_idx = idx
                    break
        if current_idx is None:
            current_idx = len(spans) - 1

    primary_start = "\033[7;1m"
    primary_end = "\033[27;22m"
    secondary_start = "\033[1m"
    secondary_end = "\033[22m"

    out: list[str] = []
    raw_cursor = 0
    for span_idx, (start_vis, end_vis) in enumerate(spans):
        if start_vis >= len(visible_start) or end_vis <= 0:
            continue
        start_raw = visible_start[start_vis]
        end_raw = visible_end[min(len(visible_end) - 1, end_vis - 1)]
        out.append(text[raw_cursor:start_raw])
        if has_current_target:
            if current_idx is not None and span_idx == current_idx:
                out.append(primary_start)
            else:
                out.append(secondary_start)
        else:
            out.append(primary_start)
        out.append(text[start_raw:end_raw])
        if has_current_target:
            if current_idx is not None and span_idx == current_idx:
                out.append(primary_end)
            else:
                out.append(secondary_end)
        else:
            out.append(primary_end)
        raw_cursor = end_raw
    out.append(text[raw_cursor:])
    return "".join(out)


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
    tree_filter_active: bool = False,
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
) -> None:
    out: list[str] = []
    out.append("\033[H\033[J")
    help_rows = help_panel_row_count(max_lines, show_help)
    content_rows = max(1, max_lines - help_rows)
    has_current_text_hit = text_search_current_line > 0 and text_search_current_column > 0

    if not browser_visible:
        line_width = max(1, width - 1)
        text_end = min(len(text_lines), text_start + content_rows)
        text_percent = _scroll_percent(text_start, len(text_lines), content_rows)
        for row in range(content_rows):
            text_idx = text_start + row
            if text_idx < len(text_lines):
                text_raw = text_lines[text_idx].rstrip("\r\n")
                if wrap_text:
                    text_raw = clip_ansi_line(text_raw, line_width)
                else:
                    text_raw = slice_ansi_line(text_raw, text_x, line_width)
                current_col = text_search_current_column if text_idx + 1 == text_search_current_line else None
                text_raw = _highlight_ansi_substrings(
                    text_raw,
                    text_search_query,
                    current_column=current_col,
                    has_current_target=has_current_text_hit,
                )
                out.append(text_raw)
                if "\033" in text_raw:
                    out.append("\033[0m")
            out.append("\r\n")
        for row in range(help_rows):
            help_text = clip_ansi_line(HELP_PANEL_TEXT_ONLY_LINES[row], line_width)
            out.append(help_text)
            if "\033" in help_text:
                out.append("\033[0m")
            out.append("\r\n")
        left_status = f"{current_path} ({text_start + 1}-{text_end}/{len(text_lines)} {text_percent:5.1f}%)"
        status = build_status_line(left_status, width)
        out.append("\033[7m")
        out.append(status)
        out.append("\033[0m")
        os.write(sys.stdout.fileno(), "".join(out).encode("utf-8", errors="replace"))
        return

    left_width = clamp_left_width(width, left_width)
    divider_width = 1
    right_width = max(1, width - left_width - divider_width - 1)

    text_end = min(len(text_lines), text_start + content_rows)
    text_percent = _scroll_percent(text_start, len(text_lines), content_rows)
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

        text_idx = text_start + row
        if text_idx < len(text_lines):
            text_raw = text_lines[text_idx].rstrip("\r\n")
            if wrap_text:
                text_raw = clip_ansi_line(text_raw, right_width)
            else:
                text_raw = slice_ansi_line(text_raw, text_x, right_width)
            current_col = text_search_current_column if text_idx + 1 == text_search_current_line else None
            text_raw = _highlight_ansi_substrings(
                text_raw,
                text_search_query,
                current_column=current_col,
                has_current_target=has_current_text_hit,
            )
            out.append(text_raw)
            if "\033" in text_raw:
                out.append("\033[0m")
        else:
            out.append("")
        out.append("\r\n")

    for row in range(help_rows):
        left_help = clip_ansi_line(HELP_PANEL_TREE_LINES[row], left_width)
        out.append(left_help)
        left_plain = ANSI_ESCAPE_RE.sub("", left_help)
        left_len = sum(char_display_width(ch, 0) for ch in left_plain)
        if left_len < left_width:
            out.append(" " * (left_width - left_len))
        out.append("\033[2m│\033[0m")

        right_help = clip_ansi_line(HELP_PANEL_TEXT_LINES[row], right_width)
        out.append(right_help)
        if "\033" in right_help:
            out.append("\033[0m")
        out.append("\r\n")

    left_status = f"{current_path} ({text_start + 1}-{text_end}/{len(text_lines)} {text_percent:5.1f}%)"
    status = build_status_line(left_status, width)
    out.append("\033[7m")
    out.append(status)
    out.append("\033[0m")

    os.write(sys.stdout.fileno(), "".join(out).encode("utf-8", errors="replace"))


def render_help_page(width: int, height: int) -> None:
    out: list[str] = []
    out.append("\033[H\033[J")

    modal_w = min(84, max(52, width - 10))
    modal_h = min(24, max(14, height - 6))
    x = max(0, (width - modal_w) // 2)
    y = max(0, (height - modal_h) // 2)
    inner_w = max(1, modal_w - 2)
    inner_h = max(1, modal_h - 2)

    title = "\033[1;38;5;45mlazyviewer help\033[0m"
    lines = [
        "",
        "\033[1;38;5;81mGeneral\033[0m",
        "  \033[38;5;229m?\033[0m toggle help   \033[38;5;229mq\033[0m/\033[38;5;229mEsc\033[0m close help",
        "  \033[38;5;229m:\033[0m command palette (fuzzy actions + Enter to run)",
        "  \033[38;5;229mCtrl+P\033[0m file filter mode, \033[38;5;229m/\033[0m content filter mode",
        "  \033[38;5;229mType/Backspace\033[0m edit query   \033[38;5;229mUp/Down\033[0m or \033[38;5;229mCtrl+J/K\033[0m move matches",
        "  \033[38;5;229mEnter\033[0m keeps content search active   \033[38;5;229mTab\033[0m edit query",
        "  \033[38;5;229mn/N\033[0m next/previous content hit",
        "  \033[38;5;229mAlt+Left/Right\033[0m jump back/forward in history",
        "  \033[38;5;229mm{key}\033[0m set named mark   \033[38;5;229m'{key}\033[0m jump to named mark",
        "  \033[38;5;229ms\033[0m symbol outline (functions/classes/imports) for current file",
        "  \033[38;5;229mt\033[0m show/hide tree pane",
        "  \033[38;5;229m.\033[0m show/hide hidden files and directories",
        "  \033[38;5;229mr\033[0m tree root -> selected directory (or selected file parent)",
        "  \033[38;5;229mR\033[0m tree root -> parent directory",
        "  \033[38;5;229mCtrl+U\033[0m/\033[38;5;229mCtrl+D\033[0m smart directory jump around opened dirs (max 10)",
        "",
        "\033[1;38;5;81mTree pane\033[0m",
        "  h/j/k/l move/select   l open/expand   h collapse/parent",
        "  Enter toggles selected directory",
        "  mouse wheel scrolls tree (when pointer is on left pane)",
        "  click select + preview   double-click toggle dir/open file",
        "",
        "\033[1;38;5;81mSource pane\033[0m",
        "  \033[38;5;229mUp/Down\033[0m line   \033[38;5;229md/u\033[0m half-page   \033[38;5;229mf/B\033[0m page   \033[38;5;229mg/G\033[0m top/bottom   \033[38;5;229m10G\033[0m goto",
        "  \033[38;5;229mw\033[0m toggle wrap   \033[38;5;229mLeft/Right\033[0m horizontal scroll (wrap off)   \033[38;5;229me\033[0m edit in $EDITOR",
        "  mouse wheel scrolls source",
        "",
        "\033[1;38;5;81mLayout\033[0m",
        "  \033[38;5;229mShift+Left/Right\033[0m resize tree pane",
        "",
        "\033[2;38;5;250mPress ? / Esc / q to close\033[0m",
    ]

    # Draw a subtle dim backdrop.
    for row in range(height):
        out.append(f"\033[{row + 1};1H\033[2m")
        out.append(" " * max(1, width - 1))
        out.append("\033[0m")

    # Rounded frame.
    out.append(f"\033[{y + 1};{x + 1}H\033[38;5;45m╭")
    out.append("─" * inner_w)
    out.append("╮\033[0m")
    for i in range(inner_h):
        out.append(f"\033[{y + 2 + i};{x + 1}H\033[38;5;45m│\033[0m")
        out.append(" " * inner_w)
        out.append("\033[38;5;45m│\033[0m")
    out.append(f"\033[{y + modal_h};{x + 1}H\033[38;5;45m╰")
    out.append("─" * inner_w)
    out.append("╯\033[0m")

    # Title
    title_x = x + max(2, (modal_w - 2 - len("lazyviewer help")) // 2)
    out.append(f"\033[{y + 1};{title_x + 1}H")
    out.append(title)

    # Body
    body_rows = min(len(lines), inner_h - 1)
    for i in range(body_rows):
        text = clip_ansi_line(lines[i], inner_w - 2)
        out.append(f"\033[{y + 2 + i};{x + 3}H")
        out.append(text)
        out.append("\033[0m")

    os.write(sys.stdout.fileno(), "".join(out).encode("utf-8", errors="replace"))
