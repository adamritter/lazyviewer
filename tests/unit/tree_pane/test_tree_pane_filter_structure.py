"""Tests for tree projection and navigation helpers.

Covers file/content filter expansion, hit indexing, and traversal utilities.
Also validates search-hit row formatting details.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.render.ansi import ANSI_ESCAPE_RE
from lazyviewer.search.content import ContentMatch
from lazyviewer.search.fuzzy import fuzzy_match_file_index, to_project_relative
from lazyviewer.tree_model import (
    TreeEntry,
    build_tree_entries,
    filter_tree_entries_for_content_matches,
    filter_tree_entries_for_files,
    find_content_hit_index,
    format_tree_entry,
    next_index_after_directory_subtree,
    next_directory_entry_index,
    next_file_entry_index,
    next_opened_directory_entry_index,
)

class TreeFilterBehaviorTestsPart1(unittest.TestCase):
    def test_format_tree_entry_appends_size_label_for_large_files_in_left_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            large_file = root / "large.bin"
            small_file = root / "small.txt"
            large_file.write_bytes(b"x" * (10 * 1024))
            small_file.write_bytes(b"x" * (9 * 1024))

            entries = build_tree_entries(
                root=root,
                expanded={root},
                show_hidden=False,
            )
            large_entry = next(entry for entry in entries if entry.path.resolve() == large_file.resolve())
            small_entry = next(entry for entry in entries if entry.path.resolve() == small_file.resolve())

            rendered_large = format_tree_entry(large_entry, root=root, expanded={root})
            rendered_small = format_tree_entry(small_entry, root=root, expanded={root})
            rendered_large_no_sizes = format_tree_entry(
                large_entry,
                root=root,
                expanded={root},
                show_size_labels=False,
            )
            plain_large = ANSI_ESCAPE_RE.sub("", rendered_large)
            plain_small = ANSI_ESCAPE_RE.sub("", rendered_small)
            plain_large_no_sizes = ANSI_ESCAPE_RE.sub("", rendered_large_no_sizes)

            self.assertIn("large.bin [10 KB]", plain_large)
            self.assertIn("\033[38;5;109m [10 KB]\033[0m", rendered_large)
            self.assertNotIn("small.txt [", plain_small)
            self.assertNotIn("large.bin [10 KB]", plain_large_no_sizes)

    def test_format_tree_entry_appends_size_label_for_large_files_in_filtered_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "src").mkdir()
            large_file = root / "src" / "large.bin"
            large_file.write_bytes(b"x" * (10 * 1024))

            entries, render_expanded = filter_tree_entries_for_files(
                root=root,
                expanded={root},
                show_hidden=False,
                matched_files=[large_file],
            )
            large_entry = next(entry for entry in entries if entry.path.resolve() == large_file.resolve())

            rendered_large = format_tree_entry(large_entry, root=root, expanded=render_expanded)
            plain_large = ANSI_ESCAPE_RE.sub("", rendered_large)

            self.assertIn("large.bin [10 KB]", plain_large)

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

    def test_next_directory_entry_index_skips_files(self) -> None:
        entries = [
            TreeEntry(path=Path("/tmp/root"), depth=0, is_dir=True),
            TreeEntry(path=Path("/tmp/root/src"), depth=1, is_dir=True),
            TreeEntry(path=Path("/tmp/root/src/main.py"), depth=2, is_dir=False),
            TreeEntry(path=Path("/tmp/root/docs"), depth=1, is_dir=True),
            TreeEntry(path=Path("/tmp/root/docs/readme.md"), depth=2, is_dir=False),
        ]

        self.assertEqual(next_directory_entry_index(entries, selected_idx=0, direction=1), 1)
        self.assertEqual(next_directory_entry_index(entries, selected_idx=2, direction=1), 3)
        self.assertEqual(next_directory_entry_index(entries, selected_idx=4, direction=-1), 3)
        self.assertEqual(next_directory_entry_index(entries, selected_idx=1, direction=-1), 0)
        self.assertEqual(next_directory_entry_index(entries, selected_idx=0, direction=-1), None)

    def test_next_opened_directory_entry_index_and_after_subtree(self) -> None:
        entries = [
            TreeEntry(path=Path("/tmp/root"), depth=0, is_dir=True),
            TreeEntry(path=Path("/tmp/root/a"), depth=1, is_dir=True),
            TreeEntry(path=Path("/tmp/root/a/file.txt"), depth=2, is_dir=False),
            TreeEntry(path=Path("/tmp/root/b"), depth=1, is_dir=True),
            TreeEntry(path=Path("/tmp/root/c"), depth=1, is_dir=True),
            TreeEntry(path=Path("/tmp/root/c/sub"), depth=2, is_dir=True),
            TreeEntry(path=Path("/tmp/root/c/sub/file.txt"), depth=3, is_dir=False),
            TreeEntry(path=Path("/tmp/root/d"), depth=1, is_dir=True),
        ]
        expanded = {
            entries[0].path.resolve(),
            entries[1].path.resolve(),
            entries[4].path.resolve(),
            entries[5].path.resolve(),
        }

        self.assertEqual(next_opened_directory_entry_index(entries, selected_idx=3, direction=-1, expanded=expanded), 1)
        self.assertEqual(next_opened_directory_entry_index(entries, selected_idx=1, direction=-1, expanded=expanded), 0)
        self.assertEqual(next_opened_directory_entry_index(entries, selected_idx=0, direction=-1, expanded=expanded), None)
        self.assertEqual(next_opened_directory_entry_index(entries, selected_idx=3, direction=1, expanded=expanded), 4)
        self.assertEqual(next_opened_directory_entry_index(entries, selected_idx=5, direction=1, expanded=expanded), None)

        self.assertEqual(next_index_after_directory_subtree(entries, directory_idx=1), 3)
        self.assertEqual(next_index_after_directory_subtree(entries, directory_idx=4), 7)
        self.assertEqual(next_index_after_directory_subtree(entries, directory_idx=5), 7)
        self.assertEqual(next_index_after_directory_subtree(entries, directory_idx=7), None)
