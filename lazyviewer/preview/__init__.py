"""Typed semantic preview component."""

from .model import (
    BinaryDocument,
    DiffDocument,
    DiffLine,
    DirectoryDocument,
    DirectoryRow,
    ErrorDocument,
    ImageDocument,
    PreviewDocument,
    PreviewRequest,
    RenderedPreview,
    TextDocument,
)
from .service import (
    BINARY_PROBE_BYTES,
    COLORIZE_MAX_FILE_BYTES,
    DIRECTORY_DEFAULT_DEPTH,
    DIRECTORY_GROWTH_STEP,
    DIRECTORY_HARD_MAX_ENTRIES,
    DIRECTORY_INITIAL_MAX_ENTRIES,
    PNG_SIGNATURE,
    PREVIEW_CACHE_MAX,
    PreviewService,
)

__all__ = [
    "BINARY_PROBE_BYTES",
    "BinaryDocument",
    "COLORIZE_MAX_FILE_BYTES",
    "DIRECTORY_DEFAULT_DEPTH",
    "DIRECTORY_GROWTH_STEP",
    "DIRECTORY_HARD_MAX_ENTRIES",
    "DIRECTORY_INITIAL_MAX_ENTRIES",
    "DiffDocument",
    "DiffLine",
    "DirectoryDocument",
    "DirectoryRow",
    "ErrorDocument",
    "ImageDocument",
    "PNG_SIGNATURE",
    "PREVIEW_CACHE_MAX",
    "PreviewDocument",
    "PreviewRequest",
    "PreviewService",
    "RenderedPreview",
    "TextDocument",
]
