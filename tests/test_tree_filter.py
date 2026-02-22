from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.tree import filter_tree_entries_for_files


class TreeFilterBehaviorTests(unittest.TestCase):
    def test_filter_tree_entries_for_files_includes_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "pkg").mkdir()
            (root / "src" / "pkg" / "main.py").write_text("print('hi')", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "readme.md").write_text("docs", encoding="utf-8")

            entries, render_expanded = filter_tree_entries_for_files(
                root=root,
                expanded={root.resolve()},
                show_hidden=False,
                matched_files=[root / "src" / "pkg" / "main.py"],
            )

            labels = []
            for entry in entries:
                if entry.path.resolve() == root.resolve():
                    labels.append((".", entry.depth, entry.is_dir))
                else:
                    labels.append(
                        (
                            entry.path.resolve().relative_to(root.resolve()).as_posix(),
                            entry.depth,
                            entry.is_dir,
                        )
                    )

            self.assertEqual(
                labels,
                [
                    (".", 0, True),
                    ("src", 1, True),
                    ("src/pkg", 2, True),
                    ("src/pkg/main.py", 3, False),
                ],
            )
            self.assertIn((root / "src").resolve(), render_expanded)
            self.assertIn((root / "src" / "pkg").resolve(), render_expanded)

    def test_filter_tree_entries_for_files_with_no_matches_keeps_only_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a", encoding="utf-8")

            entries, _render_expanded = filter_tree_entries_for_files(
                root=root,
                expanded={root.resolve()},
                show_hidden=False,
                matched_files=[],
            )

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].path.resolve(), root.resolve())
            self.assertTrue(entries[0].is_dir)

    def test_filter_tree_entries_for_files_ignores_paths_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "project"
            root.mkdir()
            outside = base / "outside.py"
            outside.write_text("x", encoding="utf-8")

            entries, _render_expanded = filter_tree_entries_for_files(
                root=root,
                expanded={root.resolve()},
                show_hidden=False,
                matched_files=[outside],
            )

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].path.resolve(), root.resolve())


if __name__ == "__main__":
    unittest.main()
