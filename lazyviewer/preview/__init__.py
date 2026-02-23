"""Public preview API.

Implementation lives in small focused modules. This package file keeps
compatibility imports, exports, and patch points used by tests.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..highlight import colorize_source
from .directory import (
    DIR_PREVIEW_CACHE_MAX,
    DIR_PREVIEW_DEFAULT_DEPTH,
    DIR_PREVIEW_GROWTH_STEP,
    DIR_PREVIEW_HARD_MAX_ENTRIES,
    DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    TREE_SIZE_LABEL_MIN_BYTES,
    _DIR_PREVIEW_CACHE,
    build_directory_preview,
    clear_directory_preview_cache,
)
from .path import (
    BINARY_PROBE_BYTES,
    COLORIZE_MAX_FILE_BYTES,
    PNG_SIGNATURE,
    RenderedPath,
    build_rendered_for_path as _build_rendered_for_path,
)


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
) -> RenderedPath:
    return _build_rendered_for_path(
        target,
        show_hidden,
        style,
        no_color,
        dir_max_depth=dir_max_depth,
        dir_max_entries=dir_max_entries,
        dir_skip_gitignored=dir_skip_gitignored,
        prefer_git_diff=prefer_git_diff,
        dir_git_status_overlay=dir_git_status_overlay,
        dir_show_size_labels=dir_show_size_labels,
        colorize_source_fn=colorize_source,
    )


__all__ = [
    "DIR_PREVIEW_DEFAULT_DEPTH",
    "DIR_PREVIEW_INITIAL_MAX_ENTRIES",
    "DIR_PREVIEW_GROWTH_STEP",
    "DIR_PREVIEW_HARD_MAX_ENTRIES",
    "DIR_PREVIEW_CACHE_MAX",
    "TREE_SIZE_LABEL_MIN_BYTES",
    "BINARY_PROBE_BYTES",
    "COLORIZE_MAX_FILE_BYTES",
    "PNG_SIGNATURE",
    "RenderedPath",
    "_DIR_PREVIEW_CACHE",
    "build_directory_preview",
    "clear_directory_preview_cache",
    "build_rendered_for_path",
]
