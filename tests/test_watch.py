from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from lazyviewer.watch import build_git_watch_signature, build_tree_watch_signature, resolve_git_paths


class WatchSignatureTests(unittest.TestCase):
    def test_tree_watch_signature_changes_for_visible_file_add_and_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            expanded = {root}

            sig_before = build_tree_watch_signature(root, expanded, show_hidden=True)
            target = root / "demo.txt"
            target.write_text("a\n", encoding="utf-8")
            sig_after_add = build_tree_watch_signature(root, expanded, show_hidden=True)
            target.write_text("bbbb\n", encoding="utf-8")
            sig_after_edit = build_tree_watch_signature(root, expanded, show_hidden=True)

            self.assertNotEqual(sig_before, sig_after_add)
            self.assertNotEqual(sig_after_add, sig_after_edit)

    def test_tree_watch_signature_ignores_hidden_changes_when_hidden_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            expanded = {root}

            sig_before = build_tree_watch_signature(root, expanded, show_hidden=False)
            (root / ".secret").write_text("x", encoding="utf-8")
            sig_after = build_tree_watch_signature(root, expanded, show_hidden=False)
            sig_hidden_on = build_tree_watch_signature(root, expanded, show_hidden=True)

            self.assertEqual(sig_before, sig_after)
            self.assertNotEqual(sig_before, sig_hidden_on)

    def test_git_watch_signature_changes_for_index_and_head_ref_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            git_dir = Path(tmp).resolve() / ".git"
            (git_dir / "refs" / "heads").mkdir(parents=True)
            (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (git_dir / "refs" / "heads" / "main").write_text("a\n", encoding="utf-8")
            (git_dir / "index").write_bytes(b"index-v1")

            sig1 = build_git_watch_signature(git_dir)
            (git_dir / "index").write_bytes(b"index-v2")
            sig2 = build_git_watch_signature(git_dir)
            (git_dir / "refs" / "heads" / "main").write_text("b\n", encoding="utf-8")
            sig3 = build_git_watch_signature(git_dir)
            (git_dir / "HEAD").write_text("deadbeef\n", encoding="utf-8")
            sig4 = build_git_watch_signature(git_dir)

            self.assertNotEqual(sig1, sig2)
            self.assertNotEqual(sig2, sig3)
            self.assertNotEqual(sig3, sig4)

    @unittest.skipIf(shutil.which("git") is None, "git is required")
    def test_resolve_git_paths_finds_repo_root_and_git_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            nested = root / "src"
            nested.mkdir()

            repo_root, git_dir = resolve_git_paths(nested)

            self.assertEqual(repo_root, root)
            self.assertIsNotNone(git_dir)
            assert git_dir is not None
            self.assertTrue(git_dir.exists())


if __name__ == "__main__":
    unittest.main()
