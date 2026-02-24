"""Tests for file-tree domain filesystem builders."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.file_tree_model import DirectoryEntry, FileEntry, build_file_tree, list_directory_children


class FileTreeFsTests(unittest.TestCase):
    def test_build_file_tree_respects_expanded_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            docs = root / "docs"
            docs.mkdir()
            nested = docs / "guide.md"
            nested.write_text("# guide\n", encoding="utf-8")

            collapsed_tree = build_file_tree(root, expanded={root}, show_hidden=False)
            collapsed_docs = next(
                child
                for child in collapsed_tree.children
                if isinstance(child, DirectoryEntry) and child.path.resolve() == docs.resolve()
            )
            self.assertEqual(collapsed_docs.children, ())

            expanded_tree = build_file_tree(root, expanded={root, docs}, show_hidden=False)
            expanded_docs = next(
                child
                for child in expanded_tree.children
                if isinstance(child, DirectoryEntry) and child.path.resolve() == docs.resolve()
            )
            self.assertTrue(any(isinstance(child, FileEntry) and child.path.resolve() == nested.resolve() for child in expanded_docs.children))

    def test_list_directory_children_can_include_doc_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            doc_file = root / "doc.py"
            plain_file = root / "plain.py"
            doc_file.write_text('"""module summary"""\nvalue = 1\n', encoding="utf-8")
            plain_file.write_text("value = 2\n", encoding="utf-8")

            children, scan_error, directory_mtime_ns = list_directory_children(
                root,
                show_hidden=False,
                include_doc_summaries=True,
            )

            self.assertIsNone(scan_error)
            self.assertIsNotNone(directory_mtime_ns)

            doc_child = next(child for child in children if child.path.resolve() == doc_file.resolve())
            plain_child = next(child for child in children if child.path.resolve() == plain_file.resolve())
            self.assertEqual(doc_child.doc_summary, "module summary")
            self.assertIsNone(plain_child.doc_summary)


if __name__ == "__main__":
    unittest.main()
