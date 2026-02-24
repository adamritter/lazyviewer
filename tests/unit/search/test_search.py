"""Tests for ripgrep JSON search parsing and error handling.

Uses a fake ``Popen`` stream to verify match extraction and ordering.
Also validates missing-rg and nonzero-exit failure reporting.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.search.content import ContentMatch, search_project_content_rg


class _FakePopen:
    def __init__(self, lines: list[str], returncode: int = 0, stderr: str = "") -> None:
        self.stdout = iter(lines)
        self.returncode = returncode
        self._stderr = stderr

    def poll(self):
        return self.returncode

    def kill(self) -> None:
        self.returncode = 0

    def communicate(self):
        return "", self._stderr


class SearchBehaviorTests(unittest.TestCase):
    def test_search_project_content_rg_parses_and_sorts_matches(self) -> None:
        root = Path("/tmp/project").resolve()
        payloads = [
            {"type": "begin", "data": {}},
            {
                "type": "match",
                "data": {
                    "path": {"text": "src/main.py"},
                    "lines": {"text": "beta = 2\n"},
                    "line_number": 20,
                    "submatches": [{"start": 1, "end": 5}],
                },
            },
            {
                "type": "match",
                "data": {
                    "path": {"text": "src/main.py"},
                    "lines": {"text": "alpha = 1\n"},
                    "line_number": 10,
                    "submatches": [{"start": 0, "end": 5}],
                },
            },
        ]
        fake = _FakePopen([json.dumps(payload) for payload in payloads], returncode=0)

        with mock.patch("lazyviewer.search.content.shutil.which", return_value="/usr/bin/rg"), mock.patch(
            "lazyviewer.search.content.subprocess.Popen",
            return_value=fake,
        ):
            matches_by_file, truncated, error = search_project_content_rg(
                root=root,
                query="alpha",
                show_hidden=False,
                skip_gitignored=False,
                max_matches=50,
                max_files=10,
            )

        self.assertIsNone(error)
        self.assertFalse(truncated)
        self.assertIn(root / "src/main.py", matches_by_file)
        matches = matches_by_file[root / "src/main.py"]
        self.assertEqual([(m.line, m.column, m.preview) for m in matches], [(10, 1, "alpha = 1"), (20, 2, "beta = 2")])

    def test_search_project_content_rg_returns_error_without_rg(self) -> None:
        with mock.patch("lazyviewer.search.content.shutil.which", return_value=None):
            matches_by_file, truncated, error = search_project_content_rg(
                root=Path("/tmp/project"),
                query="x",
                show_hidden=False,
            )
        self.assertEqual(matches_by_file, {})
        self.assertFalse(truncated)
        self.assertIsNotNone(error)

    def test_search_project_content_rg_propagates_rg_failure_without_matches(self) -> None:
        fake = _FakePopen([], returncode=2, stderr="bad pattern")
        with mock.patch("lazyviewer.search.content.shutil.which", return_value="/usr/bin/rg"), mock.patch(
            "lazyviewer.search.content.subprocess.Popen",
            return_value=fake,
        ):
            matches_by_file, truncated, error = search_project_content_rg(
                root=Path("/tmp/project"),
                query="x",
                show_hidden=False,
            )
        self.assertEqual(matches_by_file, {})
        self.assertFalse(truncated)
        self.assertEqual(error, "bad pattern")

    def test_content_match_dataclass_shape(self) -> None:
        match = ContentMatch(path=Path("/tmp/a.py"), line=3, column=7, preview="hello")
        self.assertEqual(match.path, Path("/tmp/a.py"))
        self.assertEqual(match.line, 3)
        self.assertEqual(match.column, 7)
        self.assertEqual(match.preview, "hello")


if __name__ == "__main__":
    unittest.main()
