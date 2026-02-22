from __future__ import annotations

import os
import sys
from pathlib import Path

from .ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line, slice_ansi_line
from .tree import TreeEntry, clamp_left_width, format_tree_entry

HELP_PANEL_TREE_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mTREE\033[0m",
    "\033[2;38;5;250mnav:\033[0m \033[38;5;229mh/j/k/l\033[0m move  \033[38;5;229mEnter\033[0m toggle/open",
    "\033[2;38;5;250mfilter:\033[0m \033[38;5;229mCtrl+P\033[0m files  \033[38;5;229m/\033[0m content  \033[38;5;229mEnter\033[0m open+exit  \033[38;5;229mTab\033[0m edit",
    "\033[2;38;5;250mroot/nav:\033[0m \033[38;5;229mr\033[0m set root  \033[38;5;229mR\033[0m parent root  \033[38;5;229mCtrl+U/D\033[0m jump dirs (max 10)",
)

HELP_PANEL_TEXT_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mTEXT + EXTRAS\033[0m",
    "\033[2;38;5;250mscroll:\033[0m \033[38;5;229mUp/Down\033[0m line  \033[38;5;229md/u\033[0m half  \033[38;5;229mf/B\033[0m page  \033[38;5;229mg/G/10G\033[0m",
    "\033[2;38;5;250medit:\033[0m \033[38;5;229mLeft/Right\033[0m x-scroll  \033[38;5;229mw\033[0m wrap  \033[38;5;229me\033[0m edit",
    "\033[2;38;5;250mextra:\033[0m \033[38;5;229ms\033[0m symbols  \033[38;5;229mt\033[0m tree  \033[38;5;229m.\033[0m hidden  \033[38;5;229m?\033[0m hide help  \033[38;5;229mq\033[0m quit",
)

HELP_PANEL_TEXT_ONLY_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mKEYS\033[0m",
    "\033[2;38;5;250mscroll:\033[0m \033[38;5;229mUp/Down\033[0m  \033[38;5;229md/u\033[0m  \033[38;5;229mf/B\033[0m  \033[38;5;229mg/G/10G\033[0m  \033[38;5;229mLeft/Right\033[0m",
    "\033[2;38;5;250medit:\033[0m \033[38;5;229mw\033[0m wrap  \033[38;5;229me\033[0m edit  \033[38;5;229ms\033[0m symbols  \033[38;5;229mt\033[0m tree  \033[38;5;229mr/R\033[0m root  \033[38;5;229m.\033[0m hidden",
    "\033[2;38;5;250mmeta:\033[0m \033[38;5;229mCtrl+P\033[0m file filter  \033[38;5;229m/\033[0m content filter  \033[38;5;229mCtrl+U/D\033[0m dir jump  \033[38;5;229m?\033[0m help  \033[38;5;229mq\033[0m quit",
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
) -> None:
    out: list[str] = []
    out.append("\033[H\033[J")
    help_rows = help_panel_row_count(max_lines, show_help)
    content_rows = max(1, max_lines - help_rows)

    if not browser_visible:
        line_width = max(1, width - 1)
        text_end = min(len(text_lines), text_start + content_rows)
        text_percent = 0.0 if len(text_lines) == 0 else (text_start / max(1, len(text_lines) - 1)) * 100.0
        for row in range(content_rows):
            text_idx = text_start + row
            if text_idx < len(text_lines):
                text_raw = text_lines[text_idx].rstrip("\r\n")
                if wrap_text:
                    text_raw = clip_ansi_line(text_raw, line_width)
                else:
                    text_raw = slice_ansi_line(text_raw, text_x, line_width)
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
    text_percent = 0.0 if len(text_lines) == 0 else (text_start / max(1, len(text_lines) - 1)) * 100.0
    symbol_picker_active = picker_active and picker_mode == "symbols"
    tree_filter_row_visible = tree_filter_active and not symbol_picker_active
    tree_row_offset = 1 if tree_filter_row_visible else 0
    items = picker_items if symbol_picker_active else []
    if items:
        picker_selected = max(0, min(picker_selected, len(items) - 1))
    else:
        picker_selected = 0
    picker_rows = max(1, content_rows - 1)
    max_picker_start = max(0, len(items) - picker_rows)
    picker_list_start = max(0, min(picker_list_start, max_picker_start))

    for row in range(content_rows):
        if symbol_picker_active:
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
            if tree_filter_editing:
                base = f"{tree_filter_prefix} {tree_filter_query}" if tree_filter_query else f"{tree_filter_prefix} "
                cursor = "_" if tree_filter_cursor_visible else " "
                query_text = f"\033[1;38;5;81m{base}{cursor}\033[0m"
            elif tree_filter_query:
                query_text = f"\033[1;38;5;81m{tree_filter_prefix} {tree_filter_query}\033[0m"
            else:
                query_text = f"\033[2;38;5;250m{tree_filter_prefix} {tree_filter_placeholder}\033[0m"
            tree_text = clip_ansi_line(query_text, left_width)
            if tree_filter_editing:
                tree_text = selected_with_ansi(tree_text)
            else:
                tree_text = f"\033[38;5;81m{tree_text}\033[0m"
        else:
            tree_idx = tree_start + row - tree_row_offset
            if tree_idx < len(tree_entries):
                tree_text = format_tree_entry(
                    tree_entries[tree_idx],
                    tree_root,
                    expanded,
                    git_status_overlay=git_status_overlay,
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
        "  \033[38;5;229mCtrl+P\033[0m file filter mode, \033[38;5;229m/\033[0m content filter mode",
        "  \033[38;5;229mType/Backspace\033[0m edit query   \033[38;5;229mUp/Down\033[0m or \033[38;5;229mCtrl+J/K\033[0m move matches",
        "  \033[38;5;229mEnter\033[0m use tree keys   \033[38;5;229mTab\033[0m edit query",
        "  \033[38;5;229ms\033[0m symbol outline (functions/classes/imports) for current file",
        "  \033[38;5;229mt\033[0m show/hide tree pane",
        "  \033[38;5;229m.\033[0m show/hide hidden files and directories",
        "  \033[38;5;229mr\033[0m tree root -> selected directory (or selected file parent)",
        "  \033[38;5;229mR\033[0m tree root -> parent directory",
        "  \033[38;5;229mCtrl+U\033[0m/\033[38;5;229mCtrl+D\033[0m jump to previous/next directory (max 10)",
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
