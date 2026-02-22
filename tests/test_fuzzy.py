from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from lazyviewer.fuzzy import (
    STRICT_SUBSTRING_ONLY_MIN_FILES,
    clear_project_files_cache,
    collect_project_files,
    fuzzy_match_labels,
    fuzzy_match_file_index,
    fuzzy_match_paths,
    fuzzy_score,
    to_project_relative,
)


class FuzzyBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_project_files_cache()

    def tearDown(self) -> None:
        clear_project_files_cache()

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

    def test_substring_match_turns_off_fuzzy_for_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "readme.md").write_text("docs", encoding="utf-8")
            (root / "tools").mkdir()
            (root / "tools" / "r_e_a_d_helper.py").write_text("helpers", encoding="utf-8")
            files = [root / "docs" / "readme.md", root / "tools" / "r_e_a_d_helper.py"]

            matches = fuzzy_match_paths("read", files, root, limit=10)

            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0][1], "docs/readme.md")

    def test_substring_match_turns_off_fuzzy_for_labels(self) -> None:
        labels = [
            "BetaThing",
            "B_e_t_a_Helper",
            "Other",
        ]

        matches = fuzzy_match_labels("beta", labels, limit=10)

        self.assertEqual(len(matches), 1)
        idx, label, _score = matches[0]
        self.assertEqual(idx, 0)
        self.assertEqual(label, "BetaThing")

    def test_fuzzy_match_file_index_uses_fuzzy_below_threshold(self) -> None:
        files = [Path("/tmp/alpha.py"), Path("/tmp/other.txt")]
        labels = ["alpha.py", "other.txt"]
        labels_folded = [label.casefold() for label in labels]

        matches = fuzzy_match_file_index(
            "alh",  # no substring match, fuzzy should still match alpha
            files,
            labels,
            labels_folded=labels_folded,
            limit=10,
            strict_substring_only_min_files=STRICT_SUBSTRING_ONLY_MIN_FILES,
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][1], "alpha.py")

    def test_fuzzy_match_file_index_skips_fuzzy_at_large_scale(self) -> None:
        files = [Path("/tmp/alpha.py"), Path("/tmp/other.txt")]
        labels = ["alpha.py", "other.txt"]
        labels_folded = [label.casefold() for label in labels]

        matches = fuzzy_match_file_index(
            "alh",  # would be fuzzy-only
            files,
            labels,
            labels_folded=labels_folded,
            limit=10,
            strict_substring_only_min_files=1,  # force strict mode for this test
        )

        self.assertEqual(matches, [])

    def test_fuzzy_match_file_index_strict_mode_uses_cache_order_and_limit(self) -> None:
        files = [
            Path("/tmp/docs/a-first.md"),
            Path("/tmp/src/a-second.py"),
            Path("/tmp/src/a-third.txt"),
        ]
        labels = [
            "docs/a-first.md",
            "src/a-second.py",
            "src/a-third.txt",
        ]
        labels_folded = [label.casefold() for label in labels]

        matches = fuzzy_match_file_index(
            "a",
            files,
            labels,
            labels_folded=labels_folded,
            limit=2,
            strict_substring_only_min_files=1,  # force strict mode for this test
        )

        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0][1], "docs/a-first.md")
        self.assertEqual(matches[1][1], "src/a-second.py")

    def test_fuzzy_match_file_index_strict_mode_stops_after_limit(self) -> None:
        class CountingList(list[str]):
            def __init__(self, values: list[str]) -> None:
                super().__init__(values)
                self.iterations = 0

            def __iter__(self):
                for value in super().__iter__():
                    self.iterations += 1
                    yield value

        total = 20_000
        limit = 300
        files = [Path(f"/tmp/src/{idx:05d}_alpha.py") for idx in range(total)]
        labels = [f"src/{idx:05d}_alpha.py" for idx in range(total)]
        labels_folded = CountingList([label.casefold() for label in labels])

        matches = fuzzy_match_file_index(
            "a",
            files,
            labels,
            labels_folded=labels_folded,
            limit=limit,
            strict_substring_only_min_files=1,  # force strict mode
        )

        self.assertEqual(len(matches), limit)
        self.assertEqual(labels_folded.iterations, limit)
        self.assertEqual(matches[0][1], "src/00000_alpha.py")
        self.assertEqual(matches[-1][1], "src/00299_alpha.py")

    def test_collect_project_files_prefers_rg_and_uses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")

            cp = mock.Mock(stdout="src/main.py\na.txt\n")
            with mock.patch("lazyviewer.fuzzy.shutil.which", return_value="/usr/bin/rg"), mock.patch(
                "lazyviewer.fuzzy.subprocess.run",
                return_value=cp,
            ) as run_mock:
                first = collect_project_files(root, show_hidden=False)
                second = collect_project_files(root, show_hidden=False)

            labels = [to_project_relative(path, root) for path in first]
            self.assertEqual(labels, ["a.txt", "src/main.py"])
            self.assertEqual([to_project_relative(path, root) for path in second], labels)
            self.assertEqual(run_mock.call_count, 1)

    def test_collect_project_files_falls_back_when_rg_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")

            with mock.patch("lazyviewer.fuzzy.shutil.which", return_value="/usr/bin/rg"), mock.patch(
                "lazyviewer.fuzzy.subprocess.run",
                side_effect=RuntimeError("rg failed"),
            ):
                files = collect_project_files(root, show_hidden=False)

            labels = [to_project_relative(path, root) for path in files]
            self.assertEqual(labels, ["a.txt", "src/main.py"])


if __name__ == "__main__":
    unittest.main()
