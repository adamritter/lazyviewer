"""Tests for preview generation across path types.

Covers directory truncation/caching, text sanitization, binary/image handling,
and git diff preview integration boundaries.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer import source_pane as preview
from lazyviewer.git_status import GIT_STATUS_CHANGED, GIT_STATUS_UNTRACKED

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class PreviewBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        preview._DIR_PREVIEW_CACHE.clear()

    def tearDown(self) -> None:
        preview._DIR_PREVIEW_CACHE.clear()

    def test_build_directory_preview_truncates_and_hides_dotfiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")
            (root / ".hidden.txt").write_text("h", encoding="utf-8")

            rendered, truncated = preview.build_directory_preview(
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

            rendered, truncated = preview.build_directory_preview(
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
            original_scandir = preview.os.scandir
            call_count = 0

            def counted_scandir(path):
                nonlocal call_count
                call_count += 1
                return original_scandir(path)

            with mock.patch("lazyviewer.source_pane.os.scandir", side_effect=counted_scandir):
                first = preview.build_directory_preview(root, show_hidden=False, max_depth=2, max_entries=10)
                second = preview.build_directory_preview(root, show_hidden=False, max_depth=2, max_entries=10)

            self.assertEqual(first, second)
            self.assertEqual(call_count, 1)

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

            rendered, truncated = preview.build_directory_preview(
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

            rendered, truncated = preview.build_directory_preview(
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

            rendered, truncated = preview.build_directory_preview(
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

    def test_build_rendered_for_path_file_returns_plain_text_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "demo.py"
            file_path.write_text("print('ok')\n", encoding="utf-8")

            rendered = preview.build_rendered_for_path(
                file_path,
                show_hidden=False,
                style="monokai",
                no_color=True,
            )

            self.assertFalse(rendered.is_directory)
            self.assertFalse(rendered.truncated)
            self.assertEqual(rendered.text, "print('ok')\n")

    def test_build_rendered_for_path_escapes_terminal_bell_and_escape_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "binary-ish.bin"
            # Include BEL and ESC to ensure terminal side effects are neutralized.
            file_path.write_bytes(b"ok\x07beep\x1b[31mred\n")

            rendered = preview.build_rendered_for_path(
                file_path,
                show_hidden=False,
                style="monokai",
                no_color=True,
            )

            self.assertIn("ok\\x07beep\\x1b[31mred\n", rendered.text)
            self.assertNotIn("\x07", rendered.text)
            self.assertNotIn("\x1b", rendered.text)

    def test_build_rendered_for_path_binary_with_nul_shows_binary_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "compiled.pyc"
            file_path.write_bytes(b"\x00\x01\x02abc")

            rendered = preview.build_rendered_for_path(
                file_path,
                show_hidden=False,
                style="monokai",
                no_color=False,
            )

            self.assertFalse(rendered.is_directory)
            self.assertFalse(rendered.truncated)
            self.assertIn("<binary file:", rendered.text)
            self.assertIn("compiled.pyc", rendered.text)
            self.assertIsNone(rendered.image_path)
            self.assertIsNone(rendered.image_format)

    def test_build_rendered_for_path_png_uses_image_preview_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "image.png"
            file_path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00IHDR\x00\x00\x00\x00")

            rendered = preview.build_rendered_for_path(
                file_path,
                show_hidden=False,
                style="monokai",
                no_color=False,
            )

            self.assertFalse(rendered.is_directory)
            self.assertFalse(rendered.truncated)
            self.assertEqual(rendered.image_format, "png")
            self.assertEqual(rendered.image_path, file_path.resolve())
            self.assertIn("Kitty graphics protocol", rendered.text)
            self.assertNotIn("<binary file:", rendered.text)

    def test_build_rendered_for_path_large_file_skips_colorization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "big.py"
            file_path.write_text("x = 1\n" * 60_000, encoding="utf-8")

            with mock.patch("lazyviewer.source_pane.os.isatty", return_value=True), mock.patch(
                "lazyviewer.source_pane.colorize_source"
            ) as colorize_mock:
                rendered = preview.build_rendered_for_path(
                    file_path,
                    show_hidden=False,
                    style="monokai",
                    no_color=False,
                )

            self.assertFalse(rendered.is_directory)
            self.assertFalse(rendered.truncated)
            self.assertIn("x = 1", rendered.text)
            colorize_mock.assert_not_called()

    def test_build_rendered_for_path_directory_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "b.txt").write_text("b", encoding="utf-8")

            rendered = preview.build_rendered_for_path(
                root,
                show_hidden=False,
                style="monokai",
                no_color=True,
                dir_max_entries=1,
            )
            plain = strip_ansi(rendered.text)

            self.assertTrue(rendered.is_directory)
            self.assertTrue(rendered.truncated)
            self.assertIn("... truncated after 1 entries ...", plain)

    def test_build_rendered_for_path_directory_includes_git_status_badges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text("print('x')\n", encoding="utf-8")
            overlay = {file_path.resolve(): GIT_STATUS_CHANGED}

            rendered = preview.build_rendered_for_path(
                root,
                show_hidden=False,
                style="monokai",
                no_color=True,
                dir_git_status_overlay=overlay,
            )
            plain = strip_ansi(rendered.text)

            self.assertTrue(rendered.is_directory)
            self.assertIn("demo.py [M]", plain)

    @unittest.skipIf(shutil.which("git") is None, "git is required for git diff preview tests")
    def test_build_rendered_for_path_shows_annotated_source_for_modified_git_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)
            file_path = root / "demo.py"
            file_path.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            file_path.write_text("a = 1\nb = 22\nc = 3\n", encoding="utf-8")
            rendered = preview.build_rendered_for_path(
                file_path,
                show_hidden=False,
                style="monokai",
                no_color=True,
            )
            plain = strip_ansi(rendered.text)
            self.assertTrue(rendered.is_git_diff_preview)
            self.assertEqual(
                plain.splitlines(),
                [
                    "  a = 1",
                    "- b = 2",
                    "+ b = 22",
                    "  c = 3",
                ],
            )

    @unittest.skipIf(shutil.which("git") is None, "git is required for git diff preview tests")
    def test_build_rendered_for_path_can_disable_git_diff_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)
            file_path = root / "demo.py"
            file_path.write_text("a = 1\nb = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            file_path.write_text("a = 1\nb = 22\n", encoding="utf-8")
            rendered = preview.build_rendered_for_path(
                file_path,
                show_hidden=False,
                style="monokai",
                no_color=True,
                prefer_git_diff=False,
            )
            self.assertFalse(rendered.is_git_diff_preview)
            self.assertEqual(rendered.text, "a = 1\nb = 22\n")

    @unittest.skipIf(shutil.which("git") is None, "git is required for git diff preview tests")
    def test_build_rendered_for_path_git_diff_preview_keeps_program_coloring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)
            file_path = root / "demo.py"
            file_path.write_text("x = 1\nname = 'old'\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)

            file_path.write_text("x = 1\nname = 'new'\n", encoding="utf-8")
            with mock.patch("lazyviewer.source_pane.os.isatty", return_value=True):
                rendered = preview.build_rendered_for_path(
                    file_path,
                    show_hidden=False,
                    style="monokai",
                    no_color=False,
                )

            plain = strip_ansi(rendered.text)
            self.assertTrue(rendered.is_git_diff_preview)
            self.assertIn("name = 'new'", plain)
            self.assertIn("name = 'old'", plain)
            self.assertIn("\x1b[", rendered.text)


if __name__ == "__main__":
    unittest.main()
