"""Background execution for typed semantic preview requests."""

from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
import threading
from typing import Protocol

from ..preview import PreviewDocument, PreviewRequest, PreviewService, RenderedPreview


class PreparePreview(Protocol):
    def __call__(self, document: PreviewDocument) -> RenderedPreview: ...


@dataclass(frozen=True, slots=True)
class PreviewLoaded:
    request: PreviewRequest
    document: PreviewDocument
    rendered: RenderedPreview | None = None


class PreviewWorker:
    """Single-threaded latest-request-wins semantic preview loader."""

    def __init__(
        self,
        service: PreviewService,
        *,
        prepare: PreparePreview | None = None,
    ) -> None:
        self._service = service
        self._prepare = prepare
        self._lock = threading.Lock()
        self._pending: PreviewRequest | None = None
        self._running = False
        self._results: Queue[PreviewLoaded] = Queue()

    def _run(self) -> None:
        while True:
            with self._lock:
                request = self._pending
                self._pending = None
                if request is None:
                    self._running = False
                    return
            try:
                document = self._service.load(request)
                rendered = self._prepare(document) if self._prepare is not None else None
            except Exception:
                continue
            self._results.put(PreviewLoaded(request, document, rendered))

    def schedule(self, request: PreviewRequest) -> None:
        with self._lock:
            self._pending = request
            if self._running:
                return
            self._running = True
        threading.Thread(
            target=self._run,
            name="lazyviewer-preview-loader",
            daemon=True,
        ).start()

    def drain(self) -> tuple[PreviewLoaded, ...]:
        results: list[PreviewLoaded] = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except Empty:
                return tuple(results)


__all__ = ["PreparePreview", "PreviewLoaded", "PreviewWorker"]
