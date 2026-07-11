"""Integration tests for gitignore-aware filtering.

Validates hidden/ignored behavior across file indexing, tree building, and previews.
Also checks newly created ignored paths are filtered without manual cache resets.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from lazyviewer.workspace import collect_root_file_labels
from lazyviewer.gitignore import clear_gitignore_cache
from lazyviewer.preview import DirectoryDocument, PreviewRequest, PreviewService
from lazyviewer.tree_model import build_tree_entries

@unittest.skipIf(shutil.which("git") is None, "git is required for gitignore integration tests")
class GitignoreIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_gitignore_cache()

    def tearDown(self) -> None:
        clear_gitignore_cache()

    def _init_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        (root / ".gitignore").write_text("ignored_dir/\nignored.txt\n__pycache__/\n", encoding="utf-8")
        (root / "visible.txt").write_text("ok", encoding="utf-8")
        (root / "ignored.txt").write_text("skip", encoding="utf-8")
        (root / "ignored_dir").mkdir()
        (root / "ignored_dir" / "inside.txt").write_text("skip", encoding="utf-8")
        (root / ".hidden.txt").write_text("hidden", encoding="utf-8")

    def test_collect_project_files_skips_gitignored_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)

            labels = set(collect_root_file_labels(root, show_hidden=True, skip_gitignored=True))

            self.assertIn("visible.txt", labels)
            self.assertIn(".hidden.txt", labels)
            self.assertNotIn("ignored.txt", labels)
            self.assertNotIn("ignored_dir/inside.txt", labels)

    def test_build_tree_entries_skips_gitignored_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)

            entries = build_tree_entries(
                root=root,
                expanded={root.resolve()},
                show_hidden=True,
                skip_gitignored=True,
            )
            labels = {
                "." if entry.path.resolve() == root.resolve() else entry.path.resolve().relative_to(root.resolve()).as_posix()
                for entry in entries
            }

            self.assertIn(".", labels)
            self.assertIn("visible.txt", labels)
            self.assertIn(".hidden.txt", labels)
            self.assertNotIn("ignored.txt", labels)
            self.assertNotIn("ignored_dir", labels)
            self.assertNotIn("ignored_dir/inside.txt", labels)

    def test_preview_service_skips_gitignored_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)

            document = PreviewService().load(PreviewRequest.create(
                root,
                workspace_revision="test",
                show_hidden=True,
                skip_gitignored=True,
                directory_max_depth=3,
                directory_max_entries=200,
            ))
            self.assertIsInstance(document, DirectoryDocument)
            labels = {row.path.name for row in document.rows}

            self.assertIn("visible.txt", labels)
            self.assertIn(".hidden.txt", labels)
            self.assertNotIn("ignored.txt", labels)
            self.assertNotIn("ignored_dir", labels)

    def test_newly_created_ignored_dirs_are_filtered_without_manual_cache_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._init_repo(root)

            first_entries = build_tree_entries(
                root=root,
                expanded={root.resolve()},
                show_hidden=True,
                skip_gitignored=True,
            )
            first_labels = {
                "." if entry.path.resolve() == root.resolve() else entry.path.resolve().relative_to(root.resolve()).as_posix()
                for entry in first_entries
            }
            self.assertNotIn("__pycache__", first_labels)

            pycache_dir = root / "__pycache__"
            pycache_dir.mkdir()
            (pycache_dir / "mod.cpython-312.pyc").write_bytes(b"pyc")

            second_entries = build_tree_entries(
                root=root,
                expanded={root.resolve()},
                show_hidden=True,
                skip_gitignored=True,
            )
            second_labels = {
                "." if entry.path.resolve() == root.resolve() else entry.path.resolve().relative_to(root.resolve()).as_posix()
                for entry in second_entries
            }
            self.assertNotIn("__pycache__", second_labels)


if __name__ == "__main__":
    unittest.main()
