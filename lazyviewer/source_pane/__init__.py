"""Public API surface for source-pane behavior.

This package groups all right-pane concerns:
- file/directory preview construction
- line rendering and sticky headers
- source selection and mouse interactions

The ``__init__`` module is intentionally a compatibility facade so callers can
import from ``lazyviewer.source_pane`` without depending on internal layout.
"""

from __future__ import annotations

import os

from .syntax import colorize_source
from .directory import (
    DIR_PREVIEW_CACHE_MAX,
    DIR_PREVIEW_DEFAULT_DEPTH,
    DIR_PREVIEW_GROWTH_STEP,
    DIR_PREVIEW_HARD_MAX_ENTRIES,
    DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    _DIR_PREVIEW_CACHE,
    build_directory_preview,
    clear_directory_preview_cache,
)
from ..tree_model.rendering import TREE_SIZE_LABEL_MIN_BYTES
from .interaction import (
    SourcePaneClickResult,
    SourcePaneMouseHandlers,
    SourcePaneGeometry,
    copy_selected_source_range,
)
from .renderer import SourcePaneRenderer
from .path import (
    BINARY_PROBE_BYTES,
    COLORIZE_MAX_FILE_BYTES,
    PNG_SIGNATURE,
    RenderedPath,
)

# Backward-compatible public alias.
build_rendered_for_path = RenderedPath.from_path


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
    "SourcePaneClickResult",
    "SourcePaneMouseHandlers",
    "SourcePaneGeometry",
    "SourcePaneRenderer",
    "copy_selected_source_range",
]
