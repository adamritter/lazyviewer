"""Unit tests for top-of-file doc-summary caching behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.tree_model import clear_doc_summary_cache
from lazyviewer.tree_model.doc_summary import cached_top_file_doc_summary
import lazyviewer.tree_model.doc_summary as doc_summary


class DocSummaryCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_doc_summary_cache()

    def tearDown(self) -> None:
        clear_doc_summary_cache()

    def test_cached_top_file_doc_summary_caches_none_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.py"
            path.write_text("value = 1\n", encoding="utf-8")
            size = path.stat().st_size

            with mock.patch.object(doc_summary, "top_file_doc_summary", return_value=None) as parse:
                first = cached_top_file_doc_summary(path, size)
                second = cached_top_file_doc_summary(path, size)

            self.assertIsNone(first)
            self.assertIsNone(second)
            self.assertEqual(parse.call_count, 1)

    def test_clear_doc_summary_cache_forces_reparse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "module.py"
            path.write_text('"""summary"""\n', encoding="utf-8")
            size = path.stat().st_size

            with mock.patch.object(doc_summary, "top_file_doc_summary", return_value="summary") as parse:
                first = cached_top_file_doc_summary(path, size)
                clear_doc_summary_cache()
                second = cached_top_file_doc_summary(path, size)

            self.assertEqual(first, "summary")
            self.assertEqual(second, "summary")
            self.assertEqual(parse.call_count, 2)
