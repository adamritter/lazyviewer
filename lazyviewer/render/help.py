"""Help panel content and full-screen help modal rendering.

Stores contextual keybinding text for normal and search modes.
Rendering helpers here are presentation-only and side-effect free.
"""

from __future__ import annotations

import os
import sys

from .ansi import clip_ansi_line

HELP_PANEL_TREE_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mTREE\033[0m",
    "\033[38;5;229mh/j/k/l\033[0m move  \033[38;5;229mEnter\033[0m toggle dir",
    "\033[38;5;229mCtrl+U/D\033[0m jump dirs",
    "\033[38;5;229mr\033[0m set root to root selected",
    "\033[38;5;229mR\033[0m set root to parent",
    "\033[38;5;229mCtrl+P\033[0m jump to file",
    "\033[38;5;229m/\033[0m find in all files",
    "\033[38;5;229mShift+Left/Right\033[0m resize tree",
    "\033[38;5;229mCtrl+G\033[0m lazygit  \033[38;5;229mCtrl+O\033[0m git on/off",
    "\033[38;5;229mm{key}/'{key}\033[0m marks  \033[38;5;229mAlt+Left/Right\033[0m history",
)

HELP_PANEL_TEXT_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mTEXT + EXTRAS\033[0m",
    "\033[38;5;229mUp/Down\033[0m line  \033[38;5;229md/u\033[0m half",
    "\033[38;5;229mSpace/f/B\033[0m page  \033[38;5;229mg/G/10G\033[0m jump",
    "\033[38;5;229mLeft/Right\033[0m x-scroll  \033[38;5;229mwheel L/R\033[0m x-scroll",
    "\033[38;5;229mw\033[0m wrap  \033[38;5;229me\033[0m edit",
    "\033[38;5;229m:\033[0m commands  \033[38;5;229ms\033[0m symbols",
    "\033[38;5;229mn/N/p\033[0m next/prev modification",
    "\033[38;5;229m.\033[0m hidden+ignored  \033[38;5;229mAlt+Left/Right\033[0m history",
    "\033[38;5;229m?\033[0m help  \033[38;5;229mq\033[0m quit",
)

HELP_PANEL_TEXT_ONLY_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mKEYS\033[0m",
    "\033[38;5;229mUp/Down\033[0m  \033[38;5;229md/u\033[0m  \033[38;5;229mSpace/f/B\033[0m  \033[38;5;229mg/G/10G\033[0m",
    "\033[38;5;229mLeft/Right\033[0m x-scroll  \033[38;5;229mwheel L/R\033[0m x-scroll  \033[38;5;229mw\033[0m wrap",
    "\033[38;5;229mh/j/k/l\033[0m  \033[38;5;229mEnter\033[0m  \033[38;5;229mShift+Left/Right\033[0m",
    "\033[38;5;229mCtrl+P\033[0m jump file  \033[38;5;229m/\033[0m search all files",
    "\033[38;5;229me\033[0m edit  \033[38;5;229ms\033[0m symbols  \033[38;5;229m:\033[0m commands",
    "\033[38;5;229mn/N/p\033[0m mods  \033[38;5;229mCtrl+G\033[0m lazygit  \033[38;5;229mCtrl+O\033[0m git on/off",
    "\033[38;5;229mr/R\033[0m root  \033[38;5;229mm{key}/'{key}\033[0m marks",
    "\033[38;5;229m.\033[0m hidden+ignored  \033[38;5;229mAlt+Left/Right\033[0m  \033[38;5;229m?\033[0m/\033[38;5;229mq\033[0m",
)


HELP_PANEL_SEARCH_EDIT_TREE_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mSEARCH QUERY\033[0m",
    "\033[38;5;229mType/Backspace\033[0m edit query",
    "\033[38;5;229mCtrl+U\033[0m clear query",
    "\033[38;5;229mUp/Down\033[0m or \033[38;5;229mCtrl+J/K\033[0m move hit",
    "\033[38;5;229mEnter\033[0m open selected hit",
    "\033[38;5;229mTab\033[0m switch to hit mode",
    "\033[38;5;229mEsc\033[0m close search",
    "\033[38;5;229mCtrl+?\033[0m help",
)

HELP_PANEL_SEARCH_HITS_TREE_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mSEARCH HITS\033[0m",
    "\033[38;5;229mn/N/p\033[0m next/prev hit",
    "\033[38;5;229mj/k\033[0m move hits (tree shown)",
    "\033[38;5;229mEnter\033[0m open selected hit",
    "\033[38;5;229m/\033[0m or \033[38;5;229mTab\033[0m edit query",
    "\033[38;5;229mEsc\033[0m close search",
    "\033[38;5;229mCtrl+P\033[0m switch to file filter",
    "\033[38;5;229mAlt+Left/Right\033[0m history",
)

HELP_PANEL_SEARCH_HITS_TEXT_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mSEARCH HITS + TEXT\033[0m",
    "\033[38;5;229mn/N/p\033[0m next/prev hit  \033[38;5;229mj/k\033[0m hit nav (tree)",
    "\033[38;5;229mSpace/f/B\033[0m page  \033[38;5;229mg/G/10G\033[0m jump",
    "\033[38;5;229mUp/Down\033[0m line  \033[38;5;229md/u\033[0m half",
    "\033[38;5;229mLeft/Right\033[0m x-scroll  \033[38;5;229mwheel L/R\033[0m x-scroll  \033[38;5;229mw\033[0m wrap",
    "\033[38;5;229m/\033[0m or \033[38;5;229mTab\033[0m edit query  \033[38;5;229mEnter\033[0m open hit",
    "\033[38;5;229mEsc\033[0m close search  \033[38;5;229mCtrl+P\033[0m file filter",
    "\033[38;5;229m.\033[0m hidden+ignored  \033[38;5;229mAlt+Left/Right\033[0m history",
    "\033[38;5;229m?\033[0m help  \033[38;5;229mq\033[0m quit",
)

HELP_PANEL_SEARCH_HITS_TEXT_ONLY_LINES: tuple[str, ...] = (
    "\033[1;38;5;81mSEARCH HITS\033[0m",
    "\033[38;5;229mn/N/p\033[0m next/prev  \033[38;5;229mj/k\033[0m hits (tree)",
    "\033[38;5;229mSpace/f/B\033[0m  \033[38;5;229md/u\033[0m  \033[38;5;229mg/G/10G\033[0m",
    "\033[38;5;229mUp/Down\033[0m line  \033[38;5;229mLeft/Right\033[0m x-scroll  \033[38;5;229mwheel L/R\033[0m x-scroll",
    "\033[38;5;229m/\033[0m/\033[38;5;229mTab\033[0m edit query  \033[38;5;229mEnter\033[0m open hit",
    "\033[38;5;229mEsc\033[0m close search  \033[38;5;229mCtrl+P\033[0m files",
    "\033[38;5;229m.\033[0m hidden+ignored  \033[38;5;229mAlt+Left/Right\033[0m history",
    "\033[38;5;229m?\033[0m help  \033[38;5;229mq\033[0m quit",
)


def help_panel_lines(
    tree_filter_active: bool = False,
    tree_filter_mode: str = "files",
    tree_filter_editing: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Return contextual help-line sets for tree, text, and text-only layouts."""
    if tree_filter_active and tree_filter_mode == "content":
        if tree_filter_editing:
            return (
                HELP_PANEL_SEARCH_EDIT_TREE_LINES,
                (),
                HELP_PANEL_SEARCH_HITS_TEXT_ONLY_LINES,
            )
        return (
            HELP_PANEL_SEARCH_HITS_TREE_LINES,
            HELP_PANEL_SEARCH_HITS_TEXT_LINES,
            HELP_PANEL_SEARCH_HITS_TEXT_ONLY_LINES,
        )
    return HELP_PANEL_TREE_LINES, HELP_PANEL_TEXT_LINES, HELP_PANEL_TEXT_ONLY_LINES


def help_panel_row_count(
    max_lines: int,
    show_help: bool,
    browser_visible: bool = True,
    tree_filter_active: bool = False,
    tree_filter_mode: str = "files",
    tree_filter_editing: bool = False,
) -> int:
    """Compute visible help panel height constrained by terminal rows."""
    if not show_help:
        return 0
    if max_lines <= 1:
        return 0
    tree_help_lines, text_help_lines, text_only_help_lines = help_panel_lines(
        tree_filter_active=tree_filter_active,
        tree_filter_mode=tree_filter_mode,
        tree_filter_editing=tree_filter_editing,
    )
    if browser_visible:
        required_rows = max(
            len(tree_help_lines),
            len(text_help_lines),
        )
    else:
        required_rows = len(text_only_help_lines)
    return min(required_rows, max_lines - 1)


def render_help_page(width: int, height: int) -> None:
    """Render the full-screen modal help page directly to stdout."""
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
        "  \033[38;5;229mn/N/p\033[0m content hit (search) or git-mod file (normal mode, when git is on)",
        "  \033[38;5;229mCtrl+G\033[0m open lazygit (in current tree root)",
        "  \033[38;5;229mCtrl+O\033[0m toggle git overlays, git-mod nav, and git diff preview",
        "  \033[38;5;229mAlt+Left/Right\033[0m jump back/forward in history",
        "  \033[38;5;229mm{key}\033[0m set named mark   \033[38;5;229m'{key}\033[0m jump to named mark",
        "  \033[38;5;229ms\033[0m symbol outline (functions/classes/imports) for current file",
        "  \033[38;5;229mt\033[0m show/hide tree pane",
        "  \033[38;5;229m.\033[0m show/hide hidden + gitignored files",
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
        "  \033[38;5;229mUp/Down\033[0m line   \033[38;5;229md/u\033[0m half-page   \033[38;5;229mSpace/f/B\033[0m page   \033[38;5;229mg/G\033[0m top/bottom   \033[38;5;229m10G\033[0m goto",
        "  \033[38;5;229mw\033[0m toggle wrap   \033[38;5;229mLeft/Right\033[0m horizontal scroll (wrap off)   \033[38;5;229me\033[0m edit in $EDITOR",
        "  drag in source pane to copy selected text to clipboard",
        "  mouse wheel scrolls source (left/right wheel x-scrolls when content overflows)",
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
