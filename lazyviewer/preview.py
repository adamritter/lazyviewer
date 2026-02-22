from __future__ import annotations

import os
import sys
from pathlib import Path

from .highlight import colorize_source, read_text


def build_directory_preview(root_dir: Path, show_hidden: bool, max_depth: int = 4, max_entries: int = 1200) -> str:
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
            lines_out.append(f"{branch_color}{prefix}{branch}{reset}{name_color}{child.name}{suffix}{reset}")
            emitted += 1
            if child.is_dir():
                walk(child, prefix + ("   " if last else "│  "), depth + 1)

    walk(root_dir, "", 1)
    if emitted >= max_entries:
        lines_out.append("")
        lines_out.append(f"{note_color}... truncated after {max_entries} entries ...{reset}")
    return "\n".join(lines_out)


def build_rendered_for_path(target: Path, show_hidden: bool, style: str, no_color: bool) -> str:
    if target.is_dir():
        return build_directory_preview(target, show_hidden)
    try:
        source = read_text(target)
    except Exception as exc:
        return f"{target}\n\n<error reading file: {exc}>"
    if no_color:
        return source
    if os.isatty(sys.stdout.fileno()):
        return colorize_source(source, target, style)
    return source
