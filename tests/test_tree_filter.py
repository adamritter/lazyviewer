from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.fuzzy import fuzzy_match_file_index, to_project_relative
from lazyviewer.search import ContentMatch
from lazyviewer.tree import (
    TreeEntry,
    filter_tree_entries_for_content_matches,
    filter_tree_entries_for_files,
    find_content_hit_index,
    format_tree_entry,
    next_index_after_directory_subtree,
    next_directory_entry_index,
    next_file_entry_index,
    next_opened_directory_entry_index,
)


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

    def test_large_pipeline_limits_projection_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            files = []
            for idx in range(10_000):
                bucket = idx % 25
                files.append(root / "src" / f"pkg{bucket:02d}" / f"{idx:05d}_alpha.py")

            files = sorted(files, key=lambda p: to_project_relative(p, root).casefold())
            labels = [to_project_relative(path, root) for path in files]
            labels_folded = [label.casefold() for label in labels]

            matches = fuzzy_match_file_index(
                "a",
                files,
                labels,
                labels_folded=labels_folded,
                limit=300,
                strict_substring_only_min_files=1,  # force strict mode
            )
            matched_paths = [path for path, _, _ in matches]
            entries, _render_expanded = filter_tree_entries_for_files(
                root=root,
                expanded={root},
                show_hidden=False,
                matched_files=matched_paths,
            )

            self.assertEqual(len(matches), 300)
            # root + src + up to 25 package dirs + 300 files
            self.assertLessEqual(len(entries), 327)
            self.assertEqual(entries[0].path.resolve(), root)
            self.assertTrue(all(entry.path.is_relative_to(root) for entry in entries))

    def test_filter_tree_entries_for_content_matches_adds_hit_rows_under_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            matches_by_file = {
                root / "src" / "main.py": [
                    ContentMatch(path=root / "src" / "main.py", line=10, column=3, preview="alpha = 1"),
                    ContentMatch(path=root / "src" / "main.py", line=20, column=1, preview="beta = 2"),
                ],
                root / "docs" / "readme.md": [
                    ContentMatch(path=root / "docs" / "readme.md", line=5, column=2, preview="search word"),
                ],
            }

            entries, render_expanded = filter_tree_entries_for_content_matches(
                root=root,
                expanded={root},
                matches_by_file=matches_by_file,
            )

            labels = []
            for entry in entries:
                if entry.path.resolve() == root:
                    labels.append((".", entry.depth, entry.kind, entry.line, entry.display))
                    continue
                rel = entry.path.resolve().relative_to(root).as_posix()
                labels.append((rel, entry.depth, entry.kind, entry.line, entry.display))

            self.assertEqual(
                labels,
                [
                    (".", 0, "path", None, None),
                    ("docs", 1, "path", None, None),
                    ("docs/readme.md", 2, "path", None, None),
                    ("docs/readme.md", 3, "search_hit", 5, "search word"),
                    ("src", 1, "path", None, None),
                    ("src/main.py", 2, "path", None, None),
                    ("src/main.py", 3, "search_hit", 10, "alpha = 1"),
                    ("src/main.py", 3, "search_hit", 20, "beta = 2"),
                ],
            )
            self.assertIn((root / "docs").resolve(), render_expanded)
            self.assertIn((root / "src").resolve(), render_expanded)

    def test_find_content_hit_index_prefers_exact_line_and_column(self) -> None:
        root = Path("/tmp/project").resolve()
        file_path = root / "src" / "main.py"
        entries = [
            TreeEntry(path=root, depth=0, is_dir=True),
            TreeEntry(path=root / "src", depth=1, is_dir=True),
            TreeEntry(path=file_path, depth=2, is_dir=False),
            TreeEntry(path=file_path, depth=3, is_dir=False, kind="search_hit", line=10, column=2, display="alpha"),
            TreeEntry(path=file_path, depth=3, is_dir=False, kind="search_hit", line=20, column=4, display="beta"),
            TreeEntry(path=root / "src" / "other.py", depth=2, is_dir=False),
            TreeEntry(
                path=root / "src" / "other.py",
                depth=3,
                is_dir=False,
                kind="search_hit",
                line=3,
                column=1,
                display="gamma",
            ),
        ]

        self.assertEqual(find_content_hit_index(entries, file_path, preferred_line=20, preferred_column=4), 4)

    def test_find_content_hit_index_falls_back_to_first_hit_for_file(self) -> None:
        root = Path("/tmp/project").resolve()
        file_path = root / "src" / "main.py"
        entries = [
            TreeEntry(path=root, depth=0, is_dir=True),
            TreeEntry(path=file_path, depth=1, is_dir=False),
            TreeEntry(path=file_path, depth=2, is_dir=False, kind="search_hit", line=2, column=1, display="alpha"),
            TreeEntry(path=file_path, depth=2, is_dir=False, kind="search_hit", line=5, column=1, display="beta"),
        ]

        self.assertEqual(find_content_hit_index(entries, file_path, preferred_line=100, preferred_column=100), 2)
        self.assertEqual(find_content_hit_index(entries, root / "missing.py"), None)

    def test_format_tree_entry_highlights_search_hit_substring(self) -> None:
        root = Path("/tmp/project").resolve()
        file_path = root / "src" / "main.py"
        entry = TreeEntry(
            path=file_path,
            depth=3,
            is_dir=False,
            kind="search_hit",
            display="some hello world line",
            line=12,
            column=7,
        )

        rendered = format_tree_entry(
            entry,
            root=root,
            expanded={root},
            search_query="hello",
        )

        self.assertIn("L12:7", rendered)
        self.assertIn("\033[7;1mhello\033[27;22m", rendered)


if __name__ == "__main__":
    unittest.main()
