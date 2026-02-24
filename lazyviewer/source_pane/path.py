"""Build preview payloads for files and directories.

This module decides how a selected path should be rendered in the source pane:
- directory tree previews
- binary/image placeholders
- git-diff previews for modified tracked files
- plain or syntax-colored source text
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import sys
from pathlib import Path

from .syntax import colorize_source, read_text, sanitize_terminal_text
from .diff import build_unified_diff_preview_for_path
from .directory import (
    DIR_PREVIEW_DEFAULT_DEPTH,
    DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    build_directory_preview,
)

BINARY_PROBE_BYTES = 4_096
COLORIZE_MAX_FILE_BYTES = 256_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class RenderedPath:
    """Rendered preview payload plus metadata used by runtime decisions."""

    text: str
    is_directory: bool
    truncated: bool
    image_path: Path | None = None
    image_format: str | None = None
    is_git_diff_preview: bool = False


def build_rendered_for_path(
    target: Path,
    show_hidden: bool,
    style: str,
    no_color: bool,
    dir_max_depth: int = DIR_PREVIEW_DEFAULT_DEPTH,
    dir_max_entries: int = DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    dir_skip_gitignored: bool = False,
    prefer_git_diff: bool = True,
    dir_git_status_overlay: dict[Path, int] | None = None,
    dir_show_size_labels: bool = True,
    colorize_source_fn: Callable[[str, Path, str], str] | None = None,
) -> RenderedPath:
    """Build preview content for a file or directory target.

    Resolution order for files:
    1. PNG signature -> kitty-image metadata preview
    2. NUL-byte probe -> binary placeholder text
    3. optional git-diff preview (when enabled and available)
    4. sanitized source text, optionally syntax-colored on TTY

    Directory targets delegate to ``build_directory_preview`` and report whether
    entry listing was truncated.
    """
    if target.is_dir():
        preview, truncated = build_directory_preview(
            target,
            show_hidden,
            max_depth=dir_max_depth,
            max_entries=dir_max_entries,
            skip_gitignored=dir_skip_gitignored,
            git_status_overlay=dir_git_status_overlay,
            show_size_labels=dir_show_size_labels,
        )
        return RenderedPath(text=preview, is_directory=True, truncated=truncated)

    try:
        file_size = target.stat().st_size
    except Exception:
        file_size = -1

    try:
        with target.open("rb") as handle:
            sample = handle.read(BINARY_PROBE_BYTES)
    except Exception:
        sample = b""
    if sample.startswith(PNG_SIGNATURE):
        try:
            image_path = target.resolve()
        except Exception:
            image_path = target
        return RenderedPath(
            text=f"{target}\n\n<PNG image preview via Kitty graphics protocol>",
            is_directory=False,
            truncated=False,
            image_path=image_path,
            image_format="png",
        )
    if b"\x00" in sample:
        if file_size >= 0:
            message = f"{target}\n\n<binary file: {file_size} bytes>"
        else:
            message = f"{target}\n\n<binary file>"
        return RenderedPath(text=message, is_directory=False, truncated=False)

    if prefer_git_diff:
        diff_preview = build_unified_diff_preview_for_path(
            target,
            colorize=not no_color and os.isatty(sys.stdout.fileno()),
            style=style,
        )
        if diff_preview:
            return RenderedPath(
                text=diff_preview,
                is_directory=False,
                truncated=False,
                is_git_diff_preview=True,
            )

    try:
        source = read_text(target)
    except Exception as exc:
        return RenderedPath(
            text=f"{target}\n\n<error reading file: {exc}>",
            is_directory=False,
            truncated=False,
        )
    source = sanitize_terminal_text(source)
    skip_colorize_for_size = file_size > COLORIZE_MAX_FILE_BYTES if file_size >= 0 else False
    if no_color or skip_colorize_for_size:
        return RenderedPath(text=source, is_directory=False, truncated=False)

    active_colorize_source = colorize_source_fn or colorize_source
    if os.isatty(sys.stdout.fileno()):
        return RenderedPath(
            text=active_colorize_source(source, target, style),
            is_directory=False,
            truncated=False,
        )
    return RenderedPath(text=source, is_directory=False, truncated=False)
