"""Background prefetch worker for directory-preview payloads."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue


@dataclass(frozen=True)
class DirectoryPreviewPrefetchRequest:
    """One directory-preview prefetch job."""

    request_id: int
    target: Path
    show_hidden: bool
    style: str
    no_color: bool
    dir_max_entries: int
    dir_skip_gitignored: bool
    prefer_git_diff: bool
    dir_git_status_overlay: dict[Path, int] | None
    dir_show_size_labels: bool


@dataclass(frozen=True)
class DirectoryPreviewPrefetchResult:
    """Completed prefetch payload from background worker."""

    request: DirectoryPreviewPrefetchRequest
    rendered_for_path: object


class DirectoryPreviewPrefetchScheduler:
    """Single-threaded latest-request-wins prefetch scheduler."""

    def __init__(self, build_rendered_for_path: Callable[..., object]) -> None:
        self._build_rendered_for_path = build_rendered_for_path
        self._lock = threading.Lock()
        self._pending: DirectoryPreviewPrefetchRequest | None = None
        self._running = False
        self._next_request_id = 1
        self._results: Queue[DirectoryPreviewPrefetchResult] = Queue()

    def _worker(self) -> None:
        while True:
            with self._lock:
                request = self._pending
                self._pending = None
                if request is None:
                    self._running = False
                    return

            try:
                rendered_for_path = self._build_rendered_for_path(
                    request.target,
                    request.show_hidden,
                    request.style,
                    request.no_color,
                    dir_max_entries=request.dir_max_entries,
                    dir_skip_gitignored=request.dir_skip_gitignored,
                    prefer_git_diff=request.prefer_git_diff,
                    dir_git_status_overlay=request.dir_git_status_overlay,
                    dir_show_size_labels=request.dir_show_size_labels,
                )
            except Exception:
                continue
            self._results.put(
                DirectoryPreviewPrefetchResult(
                    request=request,
                    rendered_for_path=rendered_for_path,
                )
            )

    def schedule(
        self,
        *,
        target: Path,
        show_hidden: bool,
        style: str,
        no_color: bool,
        dir_max_entries: int,
        dir_skip_gitignored: bool,
        prefer_git_diff: bool,
        dir_git_status_overlay: dict[Path, int] | None,
        dir_show_size_labels: bool,
    ) -> int:
        """Queue/replaces pending prefetch work and return request id."""
        overlay_copy = dict(dir_git_status_overlay) if dir_git_status_overlay is not None else None
        with self._lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            self._pending = DirectoryPreviewPrefetchRequest(
                request_id=request_id,
                target=target.resolve(),
                show_hidden=show_hidden,
                style=style,
                no_color=no_color,
                dir_max_entries=dir_max_entries,
                dir_skip_gitignored=dir_skip_gitignored,
                prefer_git_diff=prefer_git_diff,
                dir_git_status_overlay=overlay_copy,
                dir_show_size_labels=dir_show_size_labels,
            )
            if self._running:
                return request_id
            self._running = True

        worker = threading.Thread(
            target=self._worker,
            name="lazyviewer-dir-preview-prefetch",
            daemon=True,
        )
        worker.start()
        return request_id

    def drain_results(self) -> list[DirectoryPreviewPrefetchResult]:
        """Drain all completed prefetch results."""
        out: list[DirectoryPreviewPrefetchResult] = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except Empty:
                break
        return out


__all__ = [
    "DirectoryPreviewPrefetchRequest",
    "DirectoryPreviewPrefetchResult",
    "DirectoryPreviewPrefetchScheduler",
]
