"""Load filesystem targets into presentation-independent preview documents."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
import threading

from ..workspace import cached_top_file_doc_summary
from ..workspace.tree import list_directory_children, maybe_gitignore_matcher
from .diff import load_diff_document
from .model import (
    BinaryDocument,
    DiffDocument,
    DirectoryDocument,
    DirectoryRow,
    ErrorDocument,
    ImageDocument,
    PreviewDocument,
    PreviewRequest,
    TextDocument,
)
from .text import read_text, sanitize_text

BINARY_PROBE_BYTES = 4_096
COLORIZE_MAX_FILE_BYTES = 256_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DIRECTORY_DEFAULT_DEPTH = 32
DIRECTORY_INITIAL_MAX_ENTRIES = 1_000
DIRECTORY_GROWTH_STEP = 500
DIRECTORY_HARD_MAX_ENTRIES = 20_000
PREVIEW_CACHE_MAX = 128

DiffLoader = Callable[[Path], DiffDocument | None]


class PreviewService:
    """The sole owner of preview loading and its revision-scoped cache."""

    def __init__(
        self,
        *,
        diff_loader: DiffLoader = load_diff_document,
        cache_max: int = PREVIEW_CACHE_MAX,
    ) -> None:
        self._diff_loader = diff_loader
        self._cache_max = max(1, cache_max)
        self._cache: OrderedDict[PreviewRequest, PreviewDocument] = OrderedDict()
        self._lock = threading.RLock()

    def load(self, request: PreviewRequest) -> PreviewDocument:
        """Load one request, reusing only a result from the same workspace revision."""
        with self._lock:
            cached = self._cache.get(request)
            if cached is not None:
                self._cache.move_to_end(request)
                return cached

        document = self._load_uncached(request)
        with self._lock:
            self._cache[request] = document
            self._cache.move_to_end(request)
            while len(self._cache) > self._cache_max:
                self._cache.popitem(last=False)
        return document

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _load_uncached(self, request: PreviewRequest) -> PreviewDocument:
        target = request.target
        if target.is_dir():
            return self._load_directory(request)
        if not target.exists():
            return ErrorDocument(target, "path does not exist")

        try:
            file_size = int(target.stat().st_size)
        except OSError:
            file_size = -1
        try:
            with target.open("rb") as handle:
                sample = handle.read(BINARY_PROBE_BYTES)
        except OSError as exc:
            return ErrorDocument(target, str(exc))

        if sample.startswith(PNG_SIGNATURE):
            return ImageDocument(target, "png")
        if b"\x00" in sample:
            return BinaryDocument(target, file_size)
        if request.prefer_git_diff:
            diff = self._diff_loader(target)
            if diff is not None:
                return diff
        try:
            source = sanitize_text(read_text(target))
        except OSError as exc:
            return ErrorDocument(target, str(exc))
        return TextDocument(target, source, file_size)

    def _load_directory(self, request: PreviewRequest) -> DirectoryDocument:
        root = request.target
        overlay = dict(request.git_status)
        ignore_matcher = maybe_gitignore_matcher(root, request.skip_gitignored)
        rows: list[DirectoryRow] = []
        emitted = 0

        def walk(directory: Path, depth: int, ancestor_last: tuple[bool, ...]) -> None:
            nonlocal emitted
            if depth > request.directory_max_depth or emitted >= request.directory_max_entries:
                return
            children, scan_error, _mtime = list_directory_children(
                directory,
                request.show_hidden,
                ignore_matcher=ignore_matcher,
                git_status_overlay=overlay,
                include_doc_summaries=False,
                workspace_root=root,
            )
            if scan_error is not None:
                rows.append(
                    DirectoryRow(
                        path=directory,
                        depth=depth,
                        is_dir=True,
                        is_last=True,
                        ancestor_last=ancestor_last,
                        error=str(scan_error),
                    )
                )
                return

            for index, child in enumerate(children):
                if emitted >= request.directory_max_entries:
                    break
                is_last = index == len(children) - 1
                summary: str | None = None
                if not child.is_dir:
                    try:
                        summary = cached_top_file_doc_summary(child.path, child.file_size)
                    except OSError:
                        summary = None
                rows.append(
                    DirectoryRow(
                        path=child.path.resolve(),
                        depth=depth,
                        is_dir=child.is_dir,
                        is_last=is_last,
                        ancestor_last=ancestor_last,
                        file_size=child.file_size,
                        git_status_flags=child.git_status_flags,
                        doc_summary=summary,
                    )
                )
                emitted += 1
                if child.is_dir:
                    walk(child.path, depth + 1, (*ancestor_last, is_last))

        walk(root, 1, ())
        return DirectoryDocument(
            path=root,
            rows=tuple(rows),
            truncated=emitted >= request.directory_max_entries,
            max_entries=request.directory_max_entries,
            git_status_flags=int(overlay.get(root, 0)),
        )


__all__ = [
    "BINARY_PROBE_BYTES",
    "COLORIZE_MAX_FILE_BYTES",
    "DIRECTORY_DEFAULT_DEPTH",
    "DIRECTORY_GROWTH_STEP",
    "DIRECTORY_HARD_MAX_ENTRIES",
    "DIRECTORY_INITIAL_MAX_ENTRIES",
    "PNG_SIGNATURE",
    "PREVIEW_CACHE_MAX",
    "PreviewService",
]
