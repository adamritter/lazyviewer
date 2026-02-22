"""Scale-oriented Ctrl+P integration tests.

Exercises large-index matching limits and projected tree size constraints.
These guardrails keep file-filter performance predictable at high cardinality.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from lazyviewer.search.fuzzy import fuzzy_match_file_index
from lazyviewer.tree import filter_tree_entries_for_files


class _CountingList(list[str]):
    def __init__(self, values: list[str]) -> None:
        super().__init__(values)
        self.iterations = 0

    def __iter__(self):
        for value in super().__iter__():
            self.iterations += 1
            yield value


class CtrlPIntegrationTests(unittest.TestCase):
    def test_large_index_dense_query_stops_at_limit_and_projects_tree(self) -> None:
        root = Path("/tmp/qbrowser-test-root").resolve()
        total = 200_000
        limit = 300

        labels = [f"src/pkg{idx % 40:02d}/{idx:06d}_alpha.py" for idx in range(total)]
        files = [root / label for label in labels]
        labels_folded = _CountingList([label.casefold() for label in labels])

        matches = fuzzy_match_file_index(
            "a",
            files,
            labels,
            labels_folded=labels_folded,
            limit=limit,
            strict_substring_only_min_files=1,  # force strict substring mode
        )
        entries, _render_expanded = filter_tree_entries_for_files(
            root=root,
            expanded={root},
            show_hidden=False,
            matched_files=[path for path, _, _ in matches],
        )

        self.assertEqual(len(matches), limit)
        self.assertEqual(labels_folded.iterations, limit)
        # root + src + up to 40 package dirs + 300 file rows
        self.assertLessEqual(len(entries), 342)
        self.assertEqual(entries[0].path.resolve(), root)

    def test_large_index_sparse_query_returns_quickly_with_root_only_tree(self) -> None:
        root = Path("/tmp/qbrowser-test-root").resolve()
        total = 200_000

        labels = [f"src/pkg{idx % 40:02d}/{idx:06d}_alpha.py" for idx in range(total)]
        files = [root / label for label in labels]
        labels_folded = _CountingList([label.casefold() for label in labels])

        matches = fuzzy_match_file_index(
            "zzzzzz",
            files,
            labels,
            labels_folded=labels_folded,
            limit=300,
            strict_substring_only_min_files=1,  # force strict substring mode
        )
        entries, _render_expanded = filter_tree_entries_for_files(
            root=root,
            expanded={root},
            show_hidden=False,
            matched_files=[path for path, _, _ in matches],
        )

        self.assertEqual(matches, [])
        self.assertEqual(labels_folded.iterations, total)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path.resolve(), root)


if __name__ == "__main__":
    unittest.main()
