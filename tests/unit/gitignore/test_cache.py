"""Tests for gitignore matcher cache behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.gitignore import clear_gitignore_cache, get_gitignore_matcher


class GitignoreMatcherCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_gitignore_cache()

    def tearDown(self) -> None:
        clear_gitignore_cache()

    def test_get_gitignore_matcher_reuses_cached_result_within_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            sentinel = mock.sentinel.matcher
            with mock.patch("lazyviewer.gitignore._load_matcher", return_value=sentinel) as load_matcher:
                first = get_gitignore_matcher(root)
                second = get_gitignore_matcher(root)

            self.assertIs(first, sentinel)
            self.assertIs(second, sentinel)
            self.assertEqual(load_matcher.call_count, 1)

    def test_get_gitignore_matcher_reloads_after_root_mtime_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            first_matcher = mock.sentinel.first
            second_matcher = mock.sentinel.second
            with mock.patch(
                "lazyviewer.gitignore._load_matcher",
                side_effect=[first_matcher, second_matcher],
            ) as load_matcher:
                first = get_gitignore_matcher(root)
                (root / "new.txt").write_text("x\n", encoding="utf-8")
                second = get_gitignore_matcher(root)

            self.assertIs(first, first_matcher)
            self.assertIs(second, second_matcher)
            self.assertEqual(load_matcher.call_count, 2)

    def test_get_gitignore_matcher_reloads_after_ttl_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            first_matcher = mock.sentinel.first
            second_matcher = mock.sentinel.second
            with mock.patch(
                "lazyviewer.gitignore._load_matcher",
                side_effect=[first_matcher, second_matcher],
            ) as load_matcher, mock.patch(
                "lazyviewer.gitignore.time.monotonic",
                side_effect=[100.0, 103.0],
            ):
                first = get_gitignore_matcher(root)
                second = get_gitignore_matcher(root)

            self.assertIs(first, first_matcher)
            self.assertIs(second, second_matcher)
            self.assertEqual(load_matcher.call_count, 2)


if __name__ == "__main__":
    unittest.main()
