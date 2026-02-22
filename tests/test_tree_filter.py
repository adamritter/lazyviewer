from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.fuzzy import fuzzy_match_file_index, to_project_relative
from lazyviewer.tree import TreeEntry, filter_tree_entries_for_files, next_file_entry_index


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

    def test_next_file_entry_index_skips_directories(self) -> None:
        entries = [
            TreeEntry(path=Path("/tmp/root"), depth=0, is_dir=True),
            TreeEntry(path=Path("/tmp/root/src"), depth=1, is_dir=True),
            TreeEntry(path=Path("/tmp/root/src/main.py"), depth=2, is_dir=False),
            TreeEntry(path=Path("/tmp/root/docs"), depth=1, is_dir=True),
            TreeEntry(path=Path("/tmp/root/docs/readme.md"), depth=2, is_dir=False),
        ]

        self.assertEqual(next_file_entry_index(entries, selected_idx=0, direction=1), 2)
        self.assertEqual(next_file_entry_index(entries, selected_idx=-1, direction=1), 2)
        self.assertEqual(next_file_entry_index(entries, selected_idx=2, direction=1), 4)
        self.assertEqual(next_file_entry_index(entries, selected_idx=4, direction=1), None)
        self.assertEqual(next_file_entry_index(entries, selected_idx=4, direction=-1), 2)
        self.assertEqual(next_file_entry_index(entries, selected_idx=2, direction=-1), None)

    def test_strict_substring_and_tree_projection_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "main.md").write_text("docs", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "pkg").mkdir()
            (root / "src" / "pkg" / "main.py").write_text("print('hi')", encoding="utf-8")
            (root / "src" / "pkg" / "helper.py").write_text("print('helper')", encoding="utf-8")

            files = sorted(
                [
                    root / "docs" / "main.md",
                    root / "src" / "pkg" / "helper.py",
                    root / "src" / "pkg" / "main.py",
                ],
                key=lambda p: to_project_relative(p, root).casefold(),
            )
            labels = [to_project_relative(path, root) for path in files]
            labels_folded = [label.casefold() for label in labels]

            matches = fuzzy_match_file_index(
                "main",
                files,
                labels,
                labels_folded=labels_folded,
                limit=10,
                strict_substring_only_min_files=1,  # force strict mode
            )

            entries, render_expanded = filter_tree_entries_for_files(
                root=root,
                expanded={root.resolve()},
                show_hidden=False,
                matched_files=[path for path, _, _ in matches],
            )

            labels_out = []
            for entry in entries:
                if entry.path.resolve() == root.resolve():
                    labels_out.append((".", entry.depth, entry.is_dir))
                else:
                    labels_out.append(
                        (
                            entry.path.resolve().relative_to(root.resolve()).as_posix(),
                            entry.depth,
                            entry.is_dir,
                        )
                    )

            self.assertEqual(
                labels_out,
                [
                    (".", 0, True),
                    ("docs", 1, True),
                    ("docs/main.md", 2, False),
                    ("src", 1, True),
                    ("src/pkg", 2, True),
                    ("src/pkg/main.py", 3, False),
                ],
            )
            self.assertIn((root / "docs").resolve(), render_expanded)
            self.assertIn((root / "src").resolve(), render_expanded)
            self.assertIn((root / "src" / "pkg").resolve(), render_expanded)


if __name__ == "__main__":
    unittest.main()
