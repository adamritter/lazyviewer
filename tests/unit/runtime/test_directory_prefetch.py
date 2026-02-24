"""Tests for directory-preview background prefetch scheduler."""

from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from lazyviewer.runtime.directory_prefetch import DirectoryPreviewPrefetchScheduler


def _wait_for_results(
    scheduler: DirectoryPreviewPrefetchScheduler,
    *,
    expected_count: int,
    timeout_seconds: float = 1.0,
) -> list:
    deadline = time.monotonic() + timeout_seconds
    out: list = []
    while time.monotonic() < deadline:
        out.extend(scheduler.drain_results())
        if len(out) >= expected_count:
            break
        time.sleep(0.01)
    return out


class DirectoryPreviewPrefetchSchedulerTests(unittest.TestCase):
    def test_schedule_builds_directory_preview_in_background(self) -> None:
        calls: list[int] = []

        def build_rendered_for_path(_target: Path, _show_hidden: bool, _style: str, _no_color: bool, **kwargs):
            calls.append(int(kwargs["dir_max_entries"]))
            return SimpleNamespace(
                text="preview",
                is_directory=True,
                truncated=True,
                image_path=None,
                image_format=None,
                is_git_diff_preview=False,
            )

        scheduler = DirectoryPreviewPrefetchScheduler(build_rendered_for_path=build_rendered_for_path)
        scheduler.schedule(
            target=Path("/tmp").resolve(),
            show_hidden=False,
            style="monokai",
            no_color=False,
            dir_max_entries=40,
            dir_skip_gitignored=True,
            prefer_git_diff=True,
            dir_git_status_overlay=None,
            dir_show_size_labels=True,
        )

        results = _wait_for_results(scheduler, expected_count=1)
        self.assertEqual(calls, [40])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].request.dir_max_entries, 40)

    def test_pending_prefetch_requests_collapse_to_latest(self) -> None:
        calls: list[int] = []
        first_started = threading.Event()
        allow_first_finish = threading.Event()

        def build_rendered_for_path(_target: Path, _show_hidden: bool, _style: str, _no_color: bool, **kwargs):
            max_entries = int(kwargs["dir_max_entries"])
            if max_entries == 25:
                first_started.set()
                allow_first_finish.wait(timeout=1.0)
            calls.append(max_entries)
            return SimpleNamespace(
                text=f"preview-{max_entries}",
                is_directory=True,
                truncated=True,
                image_path=None,
                image_format=None,
                is_git_diff_preview=False,
            )

        scheduler = DirectoryPreviewPrefetchScheduler(build_rendered_for_path=build_rendered_for_path)
        common_kwargs = dict(
            target=Path("/tmp").resolve(),
            show_hidden=False,
            style="monokai",
            no_color=False,
            dir_skip_gitignored=True,
            prefer_git_diff=True,
            dir_git_status_overlay=None,
            dir_show_size_labels=True,
        )

        scheduler.schedule(dir_max_entries=25, **common_kwargs)
        self.assertTrue(first_started.wait(timeout=1.0))
        scheduler.schedule(dir_max_entries=75, **common_kwargs)
        scheduler.schedule(dir_max_entries=125, **common_kwargs)
        allow_first_finish.set()

        results = _wait_for_results(scheduler, expected_count=2)
        self.assertEqual(len(results), 2)
        request_sizes = {result.request.dir_max_entries for result in results}
        self.assertSetEqual(request_sizes, {25, 125})
        self.assertListEqual(calls, [25, 125])


if __name__ == "__main__":
    unittest.main()
