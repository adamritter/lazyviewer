from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer import preview

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

            with mock.patch("lazyviewer.preview.os.scandir", side_effect=counted_scandir):
                first = preview.build_directory_preview(root, show_hidden=False, max_depth=2, max_entries=10)
                second = preview.build_directory_preview(root, show_hidden=False, max_depth=2, max_entries=10)

            self.assertEqual(first, second)
            self.assertEqual(call_count, 1)

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


if __name__ == "__main__":
    unittest.main()
