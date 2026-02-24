"""Tests for preview generation across path types.

Covers directory truncation/caching, text sanitization, binary/image handling,
and git diff preview integration boundaries.
"""

from __future__ import annotations

import re
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer import source_pane as preview
from lazyviewer.git_status import GIT_STATUS_CHANGED, GIT_STATUS_UNTRACKED
import lazyviewer.tree_model.build as tree_model_build

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)

class PreviewBehaviorTestsPart1(unittest.TestCase):
    def setUp(self) -> None:
        preview.SourcePane._DIR_PREVIEW_CACHE.clear()

    def tearDown(self) -> None:
        preview.SourcePane._DIR_PREVIEW_CACHE.clear()

    def test_build_directory_preview_truncates_and_hides_dotfiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")
            (root / ".hidden.txt").write_text("h", encoding="utf-8")

            rendered, truncated = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
                max_depth=2,
                max_entries=1,
            )
            plain = strip_ansi(rendered)

            self.assertTrue(truncated)
            self.assertIn("... truncated after 1 entries ...", plain)
            self.assertNotIn(".hidden.txt", plain)

    def test_build_directory_preview_includes_dotfiles_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".hidden.txt").write_text("h", encoding="utf-8")

            rendered, truncated = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=True,
                max_depth=2,
                max_entries=10,
            )
            plain = strip_ansi(rendered)

            self.assertFalse(truncated)
            self.assertIn(".hidden.txt", plain)

    def test_build_directory_preview_uses_cache_for_identical_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a", encoding="utf-8")
            original_scandir = tree_model_build.os.scandir
            call_count = 0

            def counted_scandir(path):
                nonlocal call_count
                call_count += 1
                return original_scandir(path)

            with mock.patch("lazyviewer.tree_model.build.os.scandir", side_effect=counted_scandir):
                first = preview.SourcePane.build_directory_preview(
                    root,
                    show_hidden=False,
                    max_depth=2,
                    max_entries=10,
                )
                second = preview.SourcePane.build_directory_preview(
                    root,
                    show_hidden=False,
                    max_depth=2,
                    max_entries=10,
                )

            self.assertEqual(first, second)
            self.assertEqual(call_count, 1)

    def test_build_directory_preview_invalidates_cache_when_nested_directory_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            existing = nested / "existing.txt"
            existing.write_text("old\n", encoding="utf-8")

            first_rendered, _ = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
                max_depth=4,
                max_entries=100,
            )
            first_plain = strip_ansi(first_rendered)
            self.assertIn("existing.txt", first_plain)
            self.assertNotIn("new.txt", first_plain)

            (nested / "new.txt").write_text("new\n", encoding="utf-8")

            second_rendered, _ = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
                max_depth=4,
                max_entries=100,
            )
            second_plain = strip_ansi(second_rendered)
            self.assertIn("new.txt", second_plain)

    def test_build_directory_preview_invalidates_cache_when_file_summary_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target = root / "module.py"
            target.write_text("# alpha summary\nvalue = 1\n", encoding="utf-8")

            first_rendered, _ = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
                max_depth=2,
                max_entries=100,
            )
            first_plain = strip_ansi(first_rendered)
            self.assertIn("module.py  -- alpha summary", first_plain)

            previous = target.stat()
            target.write_text("# bravo summary\nvalue = 1\n", encoding="utf-8")
            os.utime(target, ns=(int(previous.st_atime_ns), int(previous.st_mtime_ns) + 1_000_000))

            second_rendered, _ = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
                max_depth=2,
                max_entries=100,
            )
            second_plain = strip_ansi(second_rendered)
            self.assertIn("module.py  -- bravo summary", second_plain)

    def test_build_directory_preview_appends_git_status_badges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "src"
            src.mkdir()
            file_path = src / "main.py"
            file_path.write_text("print('ok')\n", encoding="utf-8")

            overlay = {
                root: GIT_STATUS_CHANGED,
                src.resolve(): GIT_STATUS_UNTRACKED,
                file_path.resolve(): GIT_STATUS_CHANGED | GIT_STATUS_UNTRACKED,
            }

            rendered, truncated = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
                max_depth=3,
                max_entries=20,
                git_status_overlay=overlay,
            )
            plain = strip_ansi(rendered)

            self.assertFalse(truncated)
            self.assertIn(f"{root}/ [M]", plain)
            self.assertIn("src/ [?]", plain)
            self.assertIn("main.py [M][?]", plain)

    def test_build_directory_preview_adds_colored_size_label_for_files_at_least_10_kb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            large_file = root / "large.bin"
            small_file = root / "small.txt"
            large_file.write_bytes(b"x" * (10 * 1024))
            small_file.write_text("tiny\n", encoding="utf-8")

            rendered, truncated = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
                max_depth=2,
                max_entries=20,
            )
            plain = strip_ansi(rendered)

            self.assertFalse(truncated)
            self.assertIn("large.bin [10 KB]", plain)
            self.assertIn("\033[38;5;109m [10 KB]\033[0m", rendered)
            self.assertIn("small.txt", plain)
            self.assertNotIn("small.txt [", plain)

    def test_build_directory_preview_can_hide_size_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            large_file = root / "large.bin"
            large_file.write_bytes(b"x" * (10 * 1024))

            rendered, truncated = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
                max_depth=2,
                max_entries=20,
                show_size_labels=False,
            )
            plain = strip_ansi(rendered)

            self.assertFalse(truncated)
            self.assertIn("large.bin", plain)
            self.assertNotIn("large.bin [10 KB]", plain)

    def test_build_directory_preview_appends_top_of_file_doc_one_liner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            docstring_file = root / "docstring.py"
            comment_file = root / "comment.py"
            plain_file = root / "plain.py"
            docstring_file.write_text('"""Render widgets quickly."""\nvalue = 1\n', encoding="utf-8")
            comment_file.write_text("# Utility helpers for tests.\nvalue = 2\n", encoding="utf-8")
            plain_file.write_text("value = 3\n", encoding="utf-8")

            rendered, truncated = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
                max_depth=2,
                max_entries=20,
            )
            plain = strip_ansi(rendered)

            self.assertFalse(truncated)
            self.assertIn("docstring.py  -- Render widgets quickly.", plain)
            self.assertIn("comment.py  -- Utility helpers for tests.", plain)
            self.assertIn("plain.py", plain)
            self.assertNotIn("plain.py  --", plain)

    def test_build_directory_preview_default_depth_shows_deep_paths_when_entry_count_is_small(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            level_1 = root / "level_1"
            level_2 = level_1 / "level_2"
            level_3 = level_2 / "level_3"
            level_4 = level_3 / "level_4"
            level_4.mkdir(parents=True)
            target = level_4 / "deep.py"
            target.write_text("# deep module\nvalue = 1\n", encoding="utf-8")

            rendered, truncated = preview.SourcePane.build_directory_preview(
                root,
                show_hidden=False,
            )
            plain = strip_ansi(rendered)

            self.assertFalse(truncated)
            self.assertIn("level_4/", plain)
            self.assertIn("deep.py", plain)
            self.assertIn("deep.py  -- deep module", plain)
