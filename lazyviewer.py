#!/usr/bin/env python3
"""Print this script (or another file) in a terminal pager with syntax highlighting."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import select
import shlex
import subprocess
import sys
import shutil
import termios
import time
import tty
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "lazyviewer.json"
DOUBLE_CLICK_SECONDS = 0.35


def read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def fallback_highlight(source: str) -> str:
    try:
        import io
        import keyword
        import tokenize

        class Style:
            RESET = "\033[0m"
            BOLD = "\033[1m"
            BLUE = "\033[34m"
            GREEN = "\033[32m"
            CYAN = "\033[36m"
            YELLOW = "\033[33m"
            MAGENTA = "\033[35m"
            GRAY = "\033[90m"

        def style_for_token(tok_type: int, value: str) -> str:
            if tok_type == tokenize.STRING:
                return Style.GREEN
            if tok_type == tokenize.COMMENT:
                return Style.GRAY
            if tok_type == tokenize.NUMBER:
                return Style.CYAN
            if tok_type == tokenize.OP:
                return Style.YELLOW
            if tok_type == tokenize.NAME:
                if keyword.iskeyword(value):
                    return Style.BOLD + Style.BLUE
                if value in {"True", "False", "None"}:
                    return Style.MAGENTA
            return ""

        out = []
        for tok_type, value, _, _, _ in tokenize.generate_tokens(io.StringIO(source).readline):
            style = style_for_token(tok_type, value)
            out.append(style + value + Style.RESET if style else value)
        return "".join(out)
    except Exception:
        return source


def pygments_highlight(source: str, path: Path, style: str = "monokai") -> str | None:
    try:
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import TextLexer, get_lexer_for_filename
        from pygments.styles import get_style_by_name
    except ImportError:
        return None

    try:
        get_style_by_name(style)
    except Exception:
        style = "monokai"

    try:
        lexer = get_lexer_for_filename(path.name, source)
    except Exception:
        lexer = TextLexer()

    formatter = TerminalFormatter(style=style)
    try:
        return highlight(source, lexer, formatter)
    except Exception:
        return None


@dataclass(frozen=True)
class TreeEntry:
    path: Path
    depth: int
    is_dir: bool


def compute_left_width(total_width: int) -> int:
    if total_width <= 60:
        return max(16, total_width // 2)
    return max(20, min(40, total_width // 3))


def clamp_left_width(total_width: int, desired_left: int) -> int:
    max_possible = max(1, total_width - 2)
    min_left = max(12, min(20, total_width - 12))
    max_left = max(min_left, total_width - 12)
    max_left = min(max_left, max_possible)
    min_left = min(min_left, max_left)
    return max(min_left, min(desired_left, max_left))


def load_config() -> dict[str, object]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, object]) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def load_left_pane_percent() -> float | None:
    data = load_config()
    value = data.get("left_pane_percent")
    if not isinstance(value, (int, float)):
        return None
    if value <= 0 or value >= 100:
        return None
    return float(value)


def save_left_pane_percent(total_width: int, left_width: int) -> None:
    if total_width <= 0:
        return
    percent = max(1.0, min(99.0, (left_width / total_width) * 100.0))
    config = load_config()
    config["left_pane_percent"] = round(percent, 2)
    save_config(config)


def load_show_hidden() -> bool:
    value = load_config().get("show_hidden")
    return bool(value) if isinstance(value, bool) else False


def save_show_hidden(show_hidden: bool) -> None:
    config = load_config()
    config["show_hidden"] = bool(show_hidden)
    save_config(config)


def build_tree_entries(root: Path, expanded: set[Path], show_hidden: bool) -> list[TreeEntry]:
    root = root.resolve()
    entries: list[TreeEntry] = [TreeEntry(root, 0, True)]

    def walk(directory: Path, depth: int) -> None:
        try:
            children = list(directory.iterdir())
        except (PermissionError, OSError):
            return
        if not show_hidden:
            children = [p for p in children if not p.name.startswith(".")]
        children = sorted(children, key=lambda p: (not p.is_dir(), p.name.lower()))
        for child in children:
            is_dir = child.is_dir()
            entries.append(TreeEntry(child, depth, is_dir))
            if is_dir and child.resolve() in expanded:
                walk(child, depth + 1)

    if root in expanded:
        walk(root, 1)
    return entries


def format_tree_entry(entry: TreeEntry, root: Path, expanded: set[Path]) -> str:
    indent = "  " * entry.depth
    if entry.path == root:
        name = f"{root.name or str(root)}/"
    else:
        name = entry.path.name + ("/" if entry.is_dir else "")
    dir_color = "\033[1;34m"
    file_color = "\033[38;5;252m"
    marker_color = "\033[38;5;44m"
    reset = "\033[0m"
    if entry.is_dir:
        marker = "▾ " if entry.path.resolve() in expanded else "▸ "
        return f"{indent}{marker_color}{marker}{reset}{dir_color}{name}{reset}"
    else:
        # Align file names under the parent directory arrow column.
        indent = "  " * max(0, entry.depth - 1)
        marker = "  "
        return f"{indent}{marker}{file_color}{name}{reset}"


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
    out = []
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
        status = (status[:width - 1] if width > 1 else "") or " "
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
    text_visible = text_lines[text_start:text_end]
    text_percent = 0.0 if len(text_lines) == 0 else (text_start / max(1, len(text_lines) - 1)) * 100.0

    for row in range(max_lines):
        tree_idx = tree_start + row
        if tree_idx < len(tree_entries):
            tree_text = format_tree_entry(tree_entries[tree_idx], tree_root, expanded)
            tree_text = clip_ansi_line(tree_text, left_width)
            if tree_idx == tree_selected:
                tree_plain_selected = ANSI_ESCAPE_RE.sub("", tree_text)
                tree_text = f"\033[7m{tree_plain_selected}\033[0m"
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
    status = (status[:width - 1] if width > 1 else "") or " "
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
        out.append(f"\033[38;5;45m│\033[0m")
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


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
TAB_STOP = 8


def char_display_width(ch: str, col: int) -> int:
    if ch == "\t":
        return TAB_STOP - (col % TAB_STOP)
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in {"W", "F"}:
        return 2
    return 1


def clip_ansi_line(text: str, max_cols: int) -> str:
    if max_cols <= 0 or not text:
        return ""

    out: list[str] = []
    col = 0
    i = 0
    n = len(text)
    while i < n and col < max_cols:
        if text[i] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                out.append(match.group(0))
                i = match.end()
                continue
        ch = text[i]
        w = char_display_width(ch, col)
        if ch == "\t":
            if col + w > max_cols:
                break
            out.append(" " * w)
            col += w
            i += 1
            continue
        if col + w > max_cols:
            break
        out.append(ch)
        col += w
        i += 1

    return "".join(out)


def slice_ansi_line(text: str, start_cols: int, max_cols: int) -> str:
    if max_cols <= 0 or not text:
        return ""
    if start_cols < 0:
        start_cols = 0

    out: list[str] = []
    col = 0
    shown = 0
    i = 0
    n = len(text)
    pending_sgr = ""
    injected_style = False
    while i < n and shown < max_cols:
        if text[i] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                seq = match.group(0)
                if seq.endswith("m"):
                    pending_sgr = seq
                if col >= start_cols:
                    out.append(seq)
                i = match.end()
                continue
        ch = text[i]
        w = char_display_width(ch, col)
        if col + w <= start_cols:
            col += w
            i += 1
            continue
        if not injected_style and pending_sgr:
            out.append(pending_sgr)
            injected_style = True
        if ch == "\t":
            spaces = " " * w
            for sp in spaces:
                if shown >= max_cols:
                    break
                out.append(sp)
                shown += 1
            col += w
            i += 1
            continue
        if shown + w > max_cols:
            break
        out.append(ch)
        shown += w
        col += w
        i += 1

    return "".join(out)


def wrap_ansi_line(text: str, width: int) -> list[str]:
    if width <= 0:
        return [""]
    if not text:
        return [""]

    wrapped: list[str] = []
    chunk: list[str] = []
    col = 0
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, i)
            if match:
                chunk.append(match.group(0))
                i = match.end()
                continue

        if col >= width:
            wrapped.append("".join(chunk))
            chunk = []
            col = 0

        ch = text[i]
        w = char_display_width(ch, col)
        if ch == "\t":
            if col + w > width and chunk:
                wrapped.append("".join(chunk))
                chunk = []
                col = 0
                w = TAB_STOP
            chunk.append(" " * w)
            col += w
            i += 1
            continue
        if col + w > width and chunk:
            wrapped.append("".join(chunk))
            chunk = []
            col = 0
        chunk.append(ch)
        col += char_display_width(ch, col)
        i += 1

    wrapped.append("".join(chunk))
    return wrapped


def build_screen_lines(rendered: str, width: int) -> list[str]:
    lines = rendered.splitlines(keepends=True)
    if not lines:
        return [""]
    return lines


def read_key(fd: int, timeout_ms: int | None = None) -> str:
    if timeout_ms is not None:
        ready, _, _ = select.select([fd], [], [], max(0.0, timeout_ms / 1000.0))
        if not ready:
            return ""

    ch = os.read(fd, 1)
    if not ch:
        return ""

    if ch == b"\x15":
        return "CTRL_U"
    if ch == b"\r":
        return "ENTER_CR"
    if ch == b"\n":
        return "ENTER_LF"

    if ch != b"\x1b":
        return ch.decode("utf-8", errors="replace")

    # Escape / arrow key sequences.
    seq = os.read(fd, 1)
    if not seq:
        return "ESC"
    if seq != b"[":
        return "ESC"
    seq = os.read(fd, 1)
    if not seq:
        return "ESC"
    if seq == b"A":
        return "UP"
    if seq == b"B":
        return "DOWN"
    if seq == b"C":
        return "RIGHT"
    if seq == b"D":
        return "LEFT"
    if seq == b"<":
        # SGR mouse: ESC [ < btn ; col ; row (M/m)
        payload = []
        while True:
            part = os.read(fd, 1)
            if not part:
                return "ESC"
            if part in {b"M", b"m"}:
                break
            payload.append(part)
            if len(payload) > 64:
                return "ESC"
        try:
            btn_s, col_s, row_s = b"".join(payload).decode("ascii").split(";")
            btn = int(btn_s)
            col = int(col_s)
            row = int(row_s)
        except Exception:
            return "ESC"
        if btn == 64:
            return f"MOUSE_WHEEL_UP:{col}:{row}"
        if btn == 65:
            return f"MOUSE_WHEEL_DOWN:{col}:{row}"
        button = btn & 0b11
        if button == 0:
            suffix = "DOWN" if part == b"M" else "UP"
            return f"MOUSE_LEFT_{suffix}:{col}:{row}"
        return "MOUSE"
    if seq == b"1":
        seq2 = os.read(fd, 1)
        if not seq2:
            return "ESC"
        if seq2 == b";":
            seq3 = os.read(fd, 1)
            if not seq3:
                return "ESC"
            seq4 = os.read(fd, 1)
            if not seq4:
                return "ESC"
            if seq3 == b"2" and seq4 == b"C":
                return "SHIFT_RIGHT"
            if seq3 == b"2" and seq4 == b"D":
                return "SHIFT_LEFT"
        return "ESC"
    return "ESC"


def run_pager(content: str, path: Path, style: str, no_color: bool, nopager: bool) -> None:
    if nopager or not os.isatty(sys.stdin.fileno()):
        rendered = content
        if not no_color and os.isatty(sys.stdout.fileno()):
            rendered = pygments_highlight(content, path, style) or fallback_highlight(content)
        sys.stdout.write(content if no_color else rendered)
        return

    initial_path = path.resolve()
    current_path = initial_path
    tree_root = initial_path if initial_path.is_dir() else initial_path.parent
    expanded: set[Path] = {tree_root.resolve()}
    show_hidden = load_show_hidden()

    def build_directory_preview(root_dir: Path, max_depth: int = 4, max_entries: int = 1200) -> str:
        dir_color = "\033[1;34m"
        file_color = "\033[38;5;252m"
        branch_color = "\033[2;38;5;245m"
        note_color = "\033[2;38;5;250m"
        reset = "\033[0m"

        lines_out: list[str] = [f"{dir_color}{root_dir}/{reset}", ""]
        emitted = 0

        def walk(directory: Path, prefix: str, depth: int) -> None:
            nonlocal emitted
            if depth > max_depth or emitted >= max_entries:
                return
            try:
                children = list(directory.iterdir())
            except (PermissionError, OSError) as exc:
                lines_out.append(f"{branch_color}{prefix}└─{reset} {note_color}<error: {exc}>{reset}")
                return
            if not show_hidden:
                children = [p for p in children if not p.name.startswith(".")]
            children = sorted(children, key=lambda p: (not p.is_dir(), p.name.lower()))
            for idx, child in enumerate(children):
                if emitted >= max_entries:
                    break
                last = idx == len(children) - 1
                branch = "└─ " if last else "├─ "
                suffix = "/" if child.is_dir() else ""
                name_color = dir_color if child.is_dir() else file_color
                lines_out.append(
                    f"{branch_color}{prefix}{branch}{reset}{name_color}{child.name}{suffix}{reset}"
                )
                emitted += 1
                if child.is_dir():
                    walk(child, prefix + ("   " if last else "│  "), depth + 1)

        walk(root_dir, "", 1)
        if emitted >= max_entries:
            lines_out.append("")
            lines_out.append(f"{note_color}... truncated after {max_entries} entries ...{reset}")
        return "\n".join(lines_out)

    def build_rendered_for_path(target: Path) -> str:
        if target.is_dir():
            return build_directory_preview(target)
        try:
            source = read_text(target)
        except Exception as exc:
            return f"{target}\n\n<error reading file: {exc}>"
        if no_color:
            return source
        if os.isatty(sys.stdout.fileno()):
            return pygments_highlight(source, target, style) or fallback_highlight(source)
        return source

    def preview_selected_entry(force: bool = False) -> None:
        nonlocal current_path, rendered, lines, max_start, start, text_x
        if not tree_entries:
            return
        entry = tree_entries[selected_idx]
        selected_target = entry.path.resolve()
        if not force and selected_target == current_path:
            return
        current_path = selected_target
        rendered = build_rendered_for_path(current_path)
        lines = build_screen_lines(rendered, right_width)
        max_start = max(0, len(lines) - usable)
        start = 0
        text_x = 0

    tree_entries = build_tree_entries(tree_root, expanded, show_hidden)
    selected_path = current_path if current_path.exists() else tree_root
    selected_idx = 0
    for idx, entry in enumerate(tree_entries):
        if entry.path.resolve() == selected_path.resolve():
            selected_idx = idx
            break

    term = shutil.get_terminal_size((80, 24))
    usable = max(1, term.lines - 1)
    saved_percent = load_left_pane_percent()
    if saved_percent is None:
        initial_left = compute_left_width(term.columns)
    else:
        initial_left = int((saved_percent / 100.0) * term.columns)
    left_width = clamp_left_width(term.columns, initial_left)
    right_width = max(1, term.columns - left_width - 2)
    rendered = build_rendered_for_path(current_path)
    lines = build_screen_lines(rendered, right_width)
    max_start = max(0, len(lines) - usable)
    start = 0
    tree_start = 0
    text_x = 0
    browser_visible = True
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    saved_tty_state = termios.tcgetattr(stdin_fd)

    def enable_tui_mode() -> None:
        tty.setraw(stdin_fd, termios.TCSAFLUSH)
        # Enable mouse wheel reporting (xterm compatible) and hide cursor.
        os.write(stdout_fd, b"\x1b[?25l\x1b[?1000h\x1b[?1006h")

    def disable_tui_mode() -> None:
        os.write(stdout_fd, b"\x1b[?1000l\x1b[?1006l\x1b[?25h")
        termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, saved_tty_state)

    def launch_editor(target: Path) -> str | None:
        editor_env = os.environ.get("EDITOR", "").strip()
        if not editor_env:
            return "Cannot edit: $EDITOR is not set."
        cmd = shlex.split(editor_env)
        if not cmd:
            return "Cannot edit: $EDITOR is empty."
        disable_tui_mode()
        try:
            subprocess.run([*cmd, str(target)], check=False)
        except Exception as exc:
            return f"Failed to launch editor: {exc}"
        finally:
            enable_tui_mode()
        return None

    @contextlib.contextmanager
    def raw_mode() -> object:
        try:
            enable_tui_mode()
            yield
        finally:
            disable_tui_mode()

    with raw_mode():
        skip_next_lf = False
        last_right_width = right_width
        count_buffer = ""
        last_click_idx = -1
        last_click_time = 0.0
        show_help = False
        dirty = True
        while True:
            term = shutil.get_terminal_size((80, 24))
            usable = max(1, term.lines - 1)
            left_width = clamp_left_width(term.columns, left_width)
            right_width = max(1, term.columns - left_width - 2)
            if right_width != last_right_width:
                lines = build_screen_lines(rendered, right_width)
                last_right_width = right_width
                dirty = True
            max_start = max(0, len(lines) - usable)

            prev_tree_start = tree_start
            if selected_idx < tree_start:
                tree_start = selected_idx
            elif selected_idx >= tree_start + usable:
                tree_start = selected_idx - usable + 1
            tree_start = max(0, min(tree_start, max(0, len(tree_entries) - usable)))
            if tree_start != prev_tree_start:
                dirty = True

            if dirty:
                if show_help:
                    render_help_page(term.columns, term.lines)
                else:
                    render_dual_page(
                        lines,
                        start,
                        tree_entries,
                        tree_start,
                        selected_idx,
                        usable,
                        current_path,
                        tree_root,
                        expanded,
                        term.columns,
                        left_width,
                        text_x,
                        browser_visible,
                        show_hidden,
                    )
                dirty = False

            key = read_key(sys.stdin.fileno(), timeout_ms=120)
            if key == "":
                continue
            if skip_next_lf and key == "ENTER_LF":
                skip_next_lf = False
                continue

            if key == "ENTER_CR":
                key = "ENTER"
                skip_next_lf = True
            elif key == "ENTER_LF":
                key = "ENTER"
                skip_next_lf = False
            else:
                skip_next_lf = False

            if key.isdigit():
                count_buffer += key
                continue

            count = int(count_buffer) if count_buffer else None
            count_buffer = ""
            if key == "?":
                show_help = not show_help
                dirty = True
                continue
            if show_help:
                if key.lower() == "q" or key == "ESC" or key == "\x03":
                    show_help = False
                    dirty = True
                continue
            if key == "CTRL_U":
                old_root = tree_root.resolve()
                parent_root = old_root.parent.resolve()
                if parent_root != old_root:
                    tree_root = parent_root
                    expanded = {tree_root, old_root}
                    tree_entries = build_tree_entries(tree_root, expanded, show_hidden)
                    selected_idx = 0
                    for idx, entry in enumerate(tree_entries):
                        if entry.path.resolve() == old_root:
                            selected_idx = idx
                            break
                    tree_start = max(0, selected_idx - max(1, usable // 2))
                    dirty = True
                continue
            if key == ".":
                show_hidden = not show_hidden
                save_show_hidden(show_hidden)
                selected_path = tree_entries[selected_idx].path.resolve() if tree_entries else tree_root
                tree_entries = build_tree_entries(tree_root, expanded, show_hidden)
                selected_idx = 0
                for idx, entry in enumerate(tree_entries):
                    if entry.path.resolve() == selected_path:
                        selected_idx = idx
                        break
                preview_selected_entry(force=True)
                dirty = True
                continue
            if key.lower() == "t":
                browser_visible = not browser_visible
                dirty = True
                continue
            if key.lower() == "e":
                edit_target: Path | None = None
                if browser_visible and tree_entries:
                    selected_entry = tree_entries[selected_idx]
                    if not selected_entry.is_dir and selected_entry.path.is_file():
                        edit_target = selected_entry.path.resolve()
                if edit_target is None and current_path.is_file():
                    edit_target = current_path.resolve()
                if edit_target is None:
                    rendered = "\033[31m<cannot edit a directory>\033[0m"
                    lines = build_screen_lines(rendered, right_width)
                    max_start = max(0, len(lines) - usable)
                    start = 0
                    text_x = 0
                    dirty = True
                    continue

                error = launch_editor(edit_target)
                current_path = edit_target
                if error is None:
                    rendered = build_rendered_for_path(current_path)
                else:
                    rendered = f"\033[31m{error}\033[0m"
                lines = build_screen_lines(rendered, right_width)
                max_start = max(0, len(lines) - usable)
                start = 0
                text_x = 0
                dirty = True
                continue
            if key.lower() == "q" or key == "\x03":
                break
            if key.startswith("MOUSE_WHEEL_UP:") or key.startswith("MOUSE_WHEEL_DOWN:"):
                direction = -1 if key.startswith("MOUSE_WHEEL_UP:") else 1
                parts = key.split(":")
                col = None
                if len(parts) >= 3:
                    try:
                        col = int(parts[1])
                    except Exception:
                        col = None
                if browser_visible and col is not None and col <= left_width:
                    prev_selected = selected_idx
                    selected_idx = max(0, min(len(tree_entries) - 1, selected_idx + direction))
                    preview_selected_entry()
                    if selected_idx != prev_selected:
                        dirty = True
                else:
                    prev_start = start
                    start += direction * 3
                    start = max(0, min(start, max_start))
                    if start != prev_start:
                        dirty = True
                continue
            if key.startswith("MOUSE_LEFT_DOWN:"):
                parts = key.split(":")
                if len(parts) >= 3:
                    try:
                        col = int(parts[1])
                        row = int(parts[2])
                    except Exception:
                        col = None
                        row = None
                    if browser_visible and col is not None and row is not None and 1 <= row <= usable and col <= left_width:
                        clicked_idx = tree_start + (row - 1)
                        if 0 <= clicked_idx < len(tree_entries):
                            prev_selected = selected_idx
                            selected_idx = clicked_idx
                            preview_selected_entry()
                            if selected_idx != prev_selected:
                                dirty = True
                            now = time.monotonic()
                            is_double = clicked_idx == last_click_idx and (now - last_click_time) <= DOUBLE_CLICK_SECONDS
                            last_click_idx = clicked_idx
                            last_click_time = now
                            if is_double:
                                entry = tree_entries[selected_idx]
                                if entry.is_dir:
                                    resolved = entry.path.resolve()
                                    if resolved in expanded:
                                        expanded.remove(resolved)
                                    else:
                                        expanded.add(resolved)
                                    tree_entries = build_tree_entries(tree_root, expanded, show_hidden)
                                    selected_idx = min(selected_idx, len(tree_entries) - 1)
                                    dirty = True
                                else:
                                    current_path = entry.path.resolve()
                                    rendered = build_rendered_for_path(current_path)
                                    lines = build_screen_lines(rendered, right_width)
                                    max_start = max(0, len(lines) - usable)
                                    start = 0
                                    text_x = 0
                                    dirty = True
                continue
            if key == "SHIFT_LEFT":
                prev_left = left_width
                left_width = clamp_left_width(term.columns, left_width - 2)
                if left_width != prev_left:
                    save_left_pane_percent(term.columns, left_width)
                    right_width = max(1, term.columns - left_width - 2)
                    if right_width != last_right_width:
                        lines = build_screen_lines(rendered, right_width)
                        last_right_width = right_width
                        max_start = max(0, len(lines) - usable)
                        start = min(start, max_start)
                    dirty = True
                continue
            if key == "SHIFT_RIGHT":
                prev_left = left_width
                left_width = clamp_left_width(term.columns, left_width + 2)
                if left_width != prev_left:
                    save_left_pane_percent(term.columns, left_width)
                    right_width = max(1, term.columns - left_width - 2)
                    if right_width != last_right_width:
                        lines = build_screen_lines(rendered, right_width)
                        last_right_width = right_width
                        max_start = max(0, len(lines) - usable)
                        start = min(start, max_start)
                    dirty = True
                continue

            if browser_visible and key.lower() == "j":
                prev_selected = selected_idx
                selected_idx = min(len(tree_entries) - 1, selected_idx + 1)
                preview_selected_entry()
                if selected_idx != prev_selected:
                    dirty = True
                continue
            if browser_visible and key.lower() == "k":
                prev_selected = selected_idx
                selected_idx = max(0, selected_idx - 1)
                preview_selected_entry()
                if selected_idx != prev_selected:
                    dirty = True
                continue
            if browser_visible and key.lower() == "l":
                entry = tree_entries[selected_idx]
                if entry.is_dir:
                    resolved = entry.path.resolve()
                    if resolved not in expanded:
                        expanded.add(resolved)
                        tree_entries = build_tree_entries(tree_root, expanded, show_hidden)
                        selected_idx = min(selected_idx, len(tree_entries) - 1)
                        preview_selected_entry()
                        dirty = True
                    else:
                        next_idx = selected_idx + 1
                        if next_idx < len(tree_entries) and tree_entries[next_idx].depth > entry.depth:
                            selected_idx = next_idx
                            preview_selected_entry()
                            dirty = True
                else:
                    current_path = entry.path.resolve()
                    rendered = build_rendered_for_path(current_path)
                    lines = build_screen_lines(rendered, right_width)
                    max_start = max(0, len(lines) - usable)
                    start = 0
                    text_x = 0
                    dirty = True
                continue
            if browser_visible and key.lower() == "h":
                entry = tree_entries[selected_idx]
                if entry.is_dir and entry.path.resolve() in expanded and entry.path.resolve() != tree_root:
                    expanded.remove(entry.path.resolve())
                    tree_entries = build_tree_entries(tree_root, expanded, show_hidden)
                    selected_idx = min(selected_idx, len(tree_entries) - 1)
                    preview_selected_entry()
                    dirty = True
                elif entry.path.resolve() != tree_root:
                    parent = entry.path.parent.resolve()
                    for idx, candidate in enumerate(tree_entries):
                        if candidate.path.resolve() == parent:
                            selected_idx = idx
                            preview_selected_entry()
                            dirty = True
                            break
                continue
            if browser_visible and key == "ENTER":
                entry = tree_entries[selected_idx]
                if entry.is_dir:
                    resolved = entry.path.resolve()
                    if resolved in expanded:
                        if resolved != tree_root:
                            expanded.remove(resolved)
                    else:
                        expanded.add(resolved)
                    tree_entries = build_tree_entries(tree_root, expanded, show_hidden)
                    selected_idx = min(selected_idx, len(tree_entries) - 1)
                    preview_selected_entry()
                    dirty = True
                    continue

            prev_start = start
            prev_text_x = text_x
            if key == " " or key.lower() == "f":
                pages = count if count is not None else 1
                start += usable * max(1, pages)
            elif key.lower() == "d":
                mult = count if count is not None else 1
                start += max(1, usable // 2) * max(1, mult)
            elif key.lower() == "u":
                mult = count if count is not None else 1
                start -= max(1, usable // 2) * max(1, mult)
            elif key == "DOWN" or (not browser_visible and key.lower() == "j"):
                start += count if count is not None else 1
            elif key == "UP" or (not browser_visible and key.lower() == "k"):
                start -= count if count is not None else 1
            elif key == "g":
                if count is None:
                    start = 0
                else:
                    start = max(0, min(count - 1, max_start))
            elif key == "G":
                if count is None:
                    start = max_start
                else:
                    start = max(0, min(count - 1, max_start))
            elif key == "ENTER":
                start += count if count is not None else 1
            elif key == "B":
                pages = count if count is not None else 1
                start -= usable * max(1, pages)
            elif key == "LEFT" or (not browser_visible and key.lower() == "h"):
                step = count if count is not None else 4
                text_x = max(0, text_x - max(1, step))
            elif key == "RIGHT" or (not browser_visible and key.lower() == "l"):
                step = count if count is not None else 4
                text_x += max(1, step)
            elif key == "HOME":
                start = 0
            elif key == "END":
                start = max_start
            elif key == "ESC":
                break

            start = max(0, min(start, max_start))
            if start != prev_start or text_x != prev_text_x:
                dirty = True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print file contents in a terminal pager with syntax highlighting."
    )
    parser.add_argument("path", nargs="?", default=None, help="Path to file. Defaults to this script.")
    parser.add_argument("--style", default="monokai", help="Pygments style name (for pygments rendering).")
    parser.add_argument("--no-color", action="store_true", help="Disable color output even on TTY.")
    parser.add_argument("--nopager", action="store_true", help="Print output directly without interactive paging.")
    args = parser.parse_args()

    path = Path(args.path or Path(__file__).resolve())
    if not path.exists():
        raise SystemExit(f"Path not found: {path}")

    source = "" if path.is_dir() else read_text(path)
    run_pager(source, path, args.style, args.no_color, args.nopager)


if __name__ == "__main__":
    main()
