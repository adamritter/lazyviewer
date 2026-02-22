from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.fuzzy import (
    collect_project_files,
    fuzzy_match_labels,
    fuzzy_match_paths,
    fuzzy_score,
    to_project_relative,
)


class FuzzyBehaviorTests(unittest.TestCase):
    def test_collect_project_files_hides_hidden_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / ".dotfile").write_text("hidden", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "b.py").write_text("b", encoding="utf-8")
            (root / "src" / ".secret.py").write_text("c", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("cfg", encoding="utf-8")

            files = collect_project_files(root, show_hidden=False)
            labels = [to_project_relative(path, root) for path in files]

            self.assertEqual(labels, ["a.txt", "src/b.py"])

    def test_collect_project_files_includes_hidden_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / ".dotfile").write_text("hidden", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / ".secret.py").write_text("c", encoding="utf-8")

            files = collect_project_files(root, show_hidden=True)
            labels = {to_project_relative(path, root) for path in files}

            self.assertIn("a.txt", labels)
            self.assertIn(".dotfile", labels)
            self.assertIn("src/.secret.py", labels)

    def test_to_project_relative_falls_back_for_outside_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            root.mkdir()
            outside = base / "outside.txt"
            outside.write_text("x", encoding="utf-8")

            label = to_project_relative(outside, root)

            self.assertEqual(label, outside.as_posix())

    def test_fuzzy_score_prefers_contiguous_matches_and_rejects_missing(self) -> None:
        contiguous = fuzzy_score("abc", "abc.py")
        gapped = fuzzy_score("abc", "a_x_b_x_c.py")

        self.assertIsNotNone(contiguous)
        self.assertIsNotNone(gapped)
        self.assertGreater(contiguous, gapped)
        self.assertIsNone(fuzzy_score("zzz", "abc.py"))

    def test_fuzzy_match_paths_respects_limit_and_uses_project_relative_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "readme.md").write_text("docs", encoding="utf-8")
            files = [root / "src" / "main.py", root / "docs" / "readme.md"]

            matches = fuzzy_match_paths("srcm", files, root, limit=0)

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0][1], "src/main.py")

    def test_fuzzy_match_labels_returns_source_indices(self) -> None:
        labels = [
            "fn     L   10  alpha",
            "class  L   20  BetaThing",
            "import L    1  import os",
        ]

        matches = fuzzy_match_labels("beta", labels, limit=10)

        self.assertEqual(len(matches), 1)
        idx, label, _score = matches[0]
        self.assertEqual(idx, 1)
        self.assertEqual(label, labels[1])


if __name__ == "__main__":
    unittest.main()
