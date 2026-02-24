"""Tests for file-tree snapshot refresh hooks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.file_tree_model import build_file_tree_snapshot, refresh_file_tree_snapshot


class FileTreeSnapshotTests(unittest.TestCase):
    def test_refresh_file_tree_snapshot_detects_filesystem_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            expanded = {root}
            baseline = root / "a.py"
            baseline.write_text("a = 1\n", encoding="utf-8")

            snapshot = build_file_tree_snapshot(root, expanded, show_hidden=False)
            (root / "b.py").write_text("b = 2\n", encoding="utf-8")

            refreshed, tree_changed, git_changed = refresh_file_tree_snapshot(
                snapshot,
                root=root,
                expanded=expanded,
                show_hidden=False,
            )

            self.assertTrue(tree_changed)
            self.assertFalse(git_changed)
            self.assertNotEqual(refreshed.tree_signature, snapshot.tree_signature)

    def test_refresh_file_tree_snapshot_detects_git_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            expanded = {root}
            git_dir = root / ".git"
            (git_dir / "refs" / "heads").mkdir(parents=True)
            (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (git_dir / "refs" / "heads" / "main").write_text("a\n", encoding="utf-8")
            (git_dir / "index").write_bytes(b"index-v1")

            snapshot = build_file_tree_snapshot(root, expanded, show_hidden=False, git_dir=git_dir)
            (git_dir / "index").write_bytes(b"index-v2")

            refreshed, tree_changed, git_changed = refresh_file_tree_snapshot(
                snapshot,
                root=root,
                expanded=expanded,
                show_hidden=False,
                git_dir=git_dir,
            )

            self.assertFalse(tree_changed)
            self.assertTrue(git_changed)
            self.assertNotEqual(refreshed.git_signature, snapshot.git_signature)


if __name__ == "__main__":
    unittest.main()
