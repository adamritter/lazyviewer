"""Background execution for typed semantic preview requests."""

from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
import threading

from ..preview import PreviewDocument, PreviewRequest, PreviewService


@dataclass(frozen=True, slots=True)
class PreviewLoaded:
    request: PreviewRequest
    document: PreviewDocument


class PreviewWorker:
    """Single-threaded latest-request-wins semantic preview loader."""

    def __init__(self, service: PreviewService) -> None:
        self._service = service
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
            except Exception:
                continue
            self._results.put(PreviewLoaded(request, document))

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


__all__ = ["PreviewLoaded", "PreviewWorker"]
