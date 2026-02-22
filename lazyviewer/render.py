from __future__ import annotations

import os
import sys
from pathlib import Path

from .ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line, slice_ansi_line
from .tree import TreeEntry, clamp_left_width, format_tree_entry


def selected_with_ansi(text: str) -> str:
    """Apply selection styling without discarding existing ANSI colors."""
    if not text:
        return text

    # Keep reverse video active even when the text contains internal resets.
    return "\033[7m" + text.replace("\033[0m", "\033[0;7m") + "\033[0m"


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
    browser_visible: bool,
    show_hidden: bool,
) -> None:
    out: list[str] = []
    out.append("\033[H\033[J")

    if not browser_visible:
        line_width = max(1, width - 1)
        text_end = min(len(text_lines), text_start + max_lines)
        text_percent = 0.0 if len(text_lines) == 0 else (text_start / max(1, len(text_lines) - 1)) * 100.0
        for row in range(max_lines):
            text_idx = text_start + row
            if text_idx < len(text_lines):
                text_raw = text_lines[text_idx].rstrip("\r\n")
                text_raw = slice_ansi_line(text_raw, text_x, line_width)
                out.append(text_raw)
                if "\033" in text_raw:
                    out.append("\033[0m")
            out.append("\r\n")
        status = (
            f" {current_path} ({text_start + 1}-{text_end}/{len(text_lines)} {text_percent:5.1f}%) "
            f"[t tree] [. hidden:{'on' if show_hidden else 'off'}] "
            f"[Text: ENTER/↑/↓ or j/k, ←/→ or h/l x:{text_x}, d/u, f/Space down, B page up, g/G, 10G, e edit] [? help] [q/esc quit] "
        )
        status = (status[: width - 1] if width > 1 else "") or " "
        status = status.ljust(max(1, width - 1))
        out.append("\033[7m")
        out.append(status)
        out.append("\033[0m")
        os.write(sys.stdout.fileno(), "".join(out).encode("utf-8", errors="replace"))
        return

    left_width = clamp_left_width(width, left_width)
    divider_width = 1
    right_width = max(1, width - left_width - divider_width - 1)

    text_end = min(len(text_lines), text_start + max_lines)
    text_percent = 0.0 if len(text_lines) == 0 else (text_start / max(1, len(text_lines) - 1)) * 100.0

    for row in range(max_lines):
        tree_idx = tree_start + row
        if tree_idx < len(tree_entries):
            tree_text = format_tree_entry(tree_entries[tree_idx], tree_root, expanded)
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
            text_raw = slice_ansi_line(text_raw, text_x, right_width)
            out.append(text_raw)
            if "\033" in text_raw:
                out.append("\033[0m")
        else:
            out.append("")
        out.append("\r\n")

    status = (
        f" {current_path} ({text_start + 1}-{text_end}/{len(text_lines)} {text_percent:5.1f}%) "
        f"[t tree] [Tree: h/j/k/l, ENTER toggle] "
        f"[. hidden:{'on' if show_hidden else 'off'}] "
        f"[Resize: Shift+left/right] "
        f"[Text: ENTER/↑/↓, ←/→ x:{text_x}, d/u, f/Space down, B page up, g/G, 10G, e edit] [? help] [q/esc quit] "
    )
    status = (status[: width - 1] if width > 1 else "") or " "
    status = status.ljust(max(1, width - 1))
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

    title = "\033[1;38;5;45mqbrowser help\033[0m"
    lines = [
        "",
        "\033[1;38;5;81mGeneral\033[0m",
        "  \033[38;5;229m?\033[0m toggle help   \033[38;5;229mq\033[0m/\033[38;5;229mEsc\033[0m close help",
        "  \033[38;5;229mt\033[0m show/hide tree pane",
        "  \033[38;5;229m.\033[0m show/hide hidden files and directories",
        "  \033[38;5;229mCtrl+U\033[0m tree root -> parent directory",
        "",
        "\033[1;38;5;81mTree pane\033[0m",
        "  h/j/k/l move/select   l open/expand   h collapse/parent",
        "  Enter toggles selected directory",
        "  mouse wheel scrolls tree (when pointer is on left pane)",
        "  click select + preview   double-click toggle dir/open file",
        "",
        "\033[1;38;5;81mSource pane\033[0m",
        "  \033[38;5;229mUp/Down\033[0m line   \033[38;5;229md/u\033[0m half-page   \033[38;5;229mf/B\033[0m page   \033[38;5;229mg/G\033[0m top/bottom   \033[38;5;229m10G\033[0m goto",
        "  \033[38;5;229mLeft/Right\033[0m horizontal scroll   \033[38;5;229me\033[0m edit in $EDITOR",
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
    title_x = x + max(2, (modal_w - 2 - len("qbrowser help")) // 2)
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
