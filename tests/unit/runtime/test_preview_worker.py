"""Tests for the typed background preview worker."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from lazyviewer.preview import PreviewRequest, TextDocument
from lazyviewer.runtime.preview_worker import PreviewWorker


def _request(path: Path, revision: str) -> PreviewRequest:
    return PreviewRequest.create(
        path,
        workspace_revision=revision,
        show_hidden=False,
        skip_gitignored=True,
    )


def _await_results(worker: PreviewWorker, count: int) -> list:
    deadline = time.monotonic() + 2.0
    results: list = []
    while time.monotonic() < deadline and len(results) < count:
        results.extend(worker.drain())
        time.sleep(0.005)
    return results


class _RecordingService:
    def __init__(self, block_first: bool = False) -> None:
        self.calls: list[PreviewRequest] = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_first = block_first

    def load(self, request: PreviewRequest) -> TextDocument:
        self.calls.append(request)
        if self.block_first and len(self.calls) == 1:
            self.started.set()
            self.release.wait(timeout=1.0)
        return TextDocument(request.target, request.workspace_revision, 1)


class PreviewWorkerTests(unittest.TestCase):
    def test_loads_typed_request_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.py"
            path.write_text("a", encoding="utf-8")
            service = _RecordingService()
            worker = PreviewWorker(service)  # type: ignore[arg-type]
            request = _request(path, "r1")

            worker.schedule(request)
            results = _await_results(worker, 1)

            self.assertEqual([result.request for result in results], [request])
            self.assertEqual(results[0].document.text, "r1")

    def test_replaces_pending_work_with_latest_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.py"
            path.write_text("a", encoding="utf-8")
            service = _RecordingService(block_first=True)
            worker = PreviewWorker(service)  # type: ignore[arg-type]
            first = _request(path, "r1")
            middle = _request(path, "r2")
            latest = _request(path, "r3")

            worker.schedule(first)
            self.assertTrue(service.started.wait(timeout=1.0))
            worker.schedule(middle)
            worker.schedule(latest)
            service.release.set()
            results = _await_results(worker, 2)

            self.assertEqual(service.calls, [first, latest])
            self.assertEqual([result.request for result in results], [first, latest])


if __name__ == "__main__":
    unittest.main()
