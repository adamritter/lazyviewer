"""Tests for git overlay flags and diff-contrast rendering.

Includes real-repo scenarios for changed/untracked propagation.
Also validates readable foreground contrast on colored diff backgrounds.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from lazyviewer.render.ansi import ANSI_ESCAPE_RE
from lazyviewer.source_pane.diff import (
    _ADDED_BG_SGR,
    _apply_line_background,
    _boost_foreground_contrast_for_diff,
)
from lazyviewer.git_status import (
    GIT_STATUS_CHANGED,
    GIT_STATUS_UNTRACKED,
    collect_git_status_overlay,
)
from lazyviewer.tree_model import TreeEntry, format_tree_entry


class GitDiffPreviewColorContrastTests(unittest.TestCase):
    def test_boost_foreground_contrast_promotes_dark_or_muted_foregrounds(self) -> None:
        self.assertEqual(_boost_foreground_contrast_for_diff("90"), "38;5;246")
        self.assertEqual(_boost_foreground_contrast_for_diff("30"), "38;5;246")
        self.assertEqual(_boost_foreground_contrast_for_diff("38;5;245"), "38;5;246")
        self.assertEqual(_boost_foreground_contrast_for_diff("38;2;90;92;91"), "38;2;170;170;170")
        self.assertEqual(_boost_foreground_contrast_for_diff("2;38;5;245"), "38;5;246")

    def test_apply_line_background_recolors_low_contrast_decorator_tokens(self) -> None:
        line = "\033[90m@unittest\033[39;49;00m.skipIf"
        rendered = _apply_line_background(line, _ADDED_BG_SGR)
        self.assertIn("\033[38;5;246;48;2;36;74;52m@unittest", rendered)
        self.assertNotIn("\033[90;48;2;36;74;52m", rendered)

    def test_boost_foreground_contrast_preserves_non_muted_truecolor_foreground(self) -> None:
        self.assertEqual(
            _boost_foreground_contrast_for_diff("38;2;220;180;120"),
            "38;2;220;180;120",
        )

    def test_apply_line_background_keeps_valid_truecolor_foreground_sgr(self) -> None:
        line = "\033[38;2;220;180;120mtoken\033[0m"
        rendered = _apply_line_background(line, _ADDED_BG_SGR)
        self.assertIn("\033[38;2;220;180;120;48;2;36;74;52mtoken", rendered)
        self.assertNotIn("\033[38;220;180;120;48;2;36;74;52m", rendered)


@unittest.skipIf(shutil.which("git") is None, "git is required for git overlay tests")
class GitStatusOverlayTests(unittest.TestCase):
    def _init_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)

    def _commit_all(self, root: Path, message: str) -> None:
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)

    def test_collect_overlay_marks_changed_untracked_and_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self._init_repo(root)
            (root / "src").mkdir()
            tracked = root / "src" / "main.py"
            tracked.write_text("print('v1')\n", encoding="utf-8")
            self._commit_all(root, "initial")

            tracked.write_text("print('v2')\n", encoding="utf-8")
            untracked = root / "src" / "scratch.py"
            untracked.write_text("print('new')\n", encoding="utf-8")

            overlay = collect_git_status_overlay(root)
            self.assertTrue(overlay[tracked.resolve()] & GIT_STATUS_CHANGED)
            self.assertTrue(overlay[untracked.resolve()] & GIT_STATUS_UNTRACKED)

            src_dir = (root / "src").resolve()
            self.assertTrue(overlay[src_dir] & GIT_STATUS_CHANGED)
            self.assertTrue(overlay[src_dir] & GIT_STATUS_UNTRACKED)
            self.assertTrue(overlay[root] & GIT_STATUS_CHANGED)
            self.assertTrue(overlay[root] & GIT_STATUS_UNTRACKED)

    def test_collect_overlay_marks_files_inside_untracked_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self._init_repo(root)
            (root / "src").mkdir()
            tracked = root / "src" / "main.py"
            tracked.write_text("print('v1')\n", encoding="utf-8")
            self._commit_all(root, "initial")

            untracked_dir = root / "tests" / "unit" / "tree_model"
            untracked_dir.mkdir(parents=True)
            nested_untracked = untracked_dir / "test_doc_summary.py"
            nested_untracked.write_text("def test_demo():\n    pass\n", encoding="utf-8")

            overlay = collect_git_status_overlay(root)

            self.assertTrue(overlay[nested_untracked.resolve()] & GIT_STATUS_UNTRACKED)
            self.assertTrue(overlay[untracked_dir.resolve()] & GIT_STATUS_UNTRACKED)
            self.assertTrue(overlay[root] & GIT_STATUS_UNTRACKED)

    def test_collect_overlay_filters_results_to_requested_tree_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self._init_repo(root)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "src" / "main.py").write_text("print('x')\n", encoding="utf-8")
            (root / "docs" / "readme.md").write_text("docs\n", encoding="utf-8")
            self._commit_all(root, "initial")

            changed_in_tree = root / "src" / "main.py"
            changed_in_tree.write_text("print('changed')\n", encoding="utf-8")
            changed_outside_tree = root / "docs" / "readme.md"
            changed_outside_tree.write_text("changed\n", encoding="utf-8")
            (root / "docs" / "extra.md").write_text("new\n", encoding="utf-8")

            src_root = (root / "src").resolve()
            overlay = collect_git_status_overlay(src_root)
            self.assertIn(changed_in_tree.resolve(), overlay)
            self.assertNotIn(changed_outside_tree.resolve(), overlay)
            self.assertNotIn((root / "docs" / "extra.md").resolve(), overlay)
            self.assertNotIn(root, overlay)
            self.assertTrue(overlay[src_root] & GIT_STATUS_CHANGED)

    def test_format_tree_entry_appends_git_badges(self) -> None:
        root = Path("/tmp/qbrowser-git-overlay").resolve()
        file_entry = TreeEntry(path=root / "src" / "main.py", depth=2, is_dir=False)
        dir_entry = TreeEntry(path=root / "src", depth=1, is_dir=True)
        overlay = {
            file_entry.path.resolve(): GIT_STATUS_CHANGED | GIT_STATUS_UNTRACKED,
            dir_entry.path.resolve(): GIT_STATUS_CHANGED,
        }

        file_plain = ANSI_ESCAPE_RE.sub(
            "",
            format_tree_entry(file_entry, root, expanded={root}, git_status_overlay=overlay),
        )
        dir_plain = ANSI_ESCAPE_RE.sub(
            "",
            format_tree_entry(dir_entry, root, expanded={root, dir_entry.path.resolve()}, git_status_overlay=overlay),
        )

        self.assertIn("[M][?]", file_plain)
        self.assertIn("[M]", dir_plain)


if __name__ == "__main__":
    unittest.main()
