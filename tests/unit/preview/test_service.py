"""Semantic preview loading tests."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.git_status import GIT_STATUS_CHANGED, GIT_STATUS_UNTRACKED
from lazyviewer.preview import (
    BinaryDocument,
    DiffDocument,
    DirectoryDocument,
    ImageDocument,
    PreviewRequest,
    PreviewService,
    TextDocument,
)


def _request(path: Path, revision: str = "r1", **kwargs) -> PreviewRequest:
    defaults = {
        "show_hidden": False,
        "skip_gitignored": False,
    }
    defaults.update(kwargs)
    return PreviewRequest.create(path, workspace_revision=revision, **defaults)


class PreviewServiceTests(unittest.TestCase):
    def test_loads_sanitized_text_without_terminal_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_bytes(b"ok\x07beep\x1b[31mred\n")

            document = PreviewService().load(_request(path, prefer_git_diff=False))

            self.assertIsInstance(document, TextDocument)
            self.assertEqual(document.text, "ok\\x07beep\\x1b[31mred\n")

    def test_classifies_binary_and_png_files_semantically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "compiled.pyc"
            image = root / "image.png"
            binary.write_bytes(b"\x00\x01abc")
            image.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
            service = PreviewService()

            binary_document = service.load(_request(binary))
            image_document = service.load(_request(image))

            self.assertIsInstance(binary_document, BinaryDocument)
            self.assertEqual(binary_document.file_size, 5)
            self.assertEqual(image_document, ImageDocument(image.resolve(), "png"))

    def test_directory_rows_retain_paths_metadata_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            hidden = root / ".hidden"
            visible = root / "visible.py"
            second = root / "z.txt"
            hidden.write_text("hidden", encoding="utf-8")
            visible.write_text("# useful module\nvalue = 1\n", encoding="utf-8")
            second.write_text("z", encoding="utf-8")
            overlay = {
                root: GIT_STATUS_CHANGED,
                visible: GIT_STATUS_CHANGED | GIT_STATUS_UNTRACKED,
            }

            document = PreviewService().load(
                _request(root, directory_max_entries=1, git_status=overlay)
            )

            self.assertIsInstance(document, DirectoryDocument)
            self.assertTrue(document.truncated)
            self.assertEqual(document.git_status_flags, GIT_STATUS_CHANGED)
            self.assertEqual([row.path for row in document.rows], [visible])
            self.assertEqual(document.rows[0].doc_summary, "useful module")
            self.assertEqual(
                document.rows[0].git_status_flags,
                GIT_STATUS_CHANGED | GIT_STATUS_UNTRACKED,
            )

    def test_directory_cache_is_scoped_by_workspace_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            (nested / "old.txt").write_text("old", encoding="utf-8")
            service = PreviewService()
            first_request = _request(root, "r1", directory_max_depth=4)
            first = service.load(first_request)
            (nested / "new.txt").write_text("new", encoding="utf-8")

            same_revision = service.load(first_request)
            next_revision = service.load(_request(root, "r2", directory_max_depth=4))

            self.assertIs(first, same_revision)
            self.assertNotIn("new.txt", [row.path.name for row in same_revision.rows])
            self.assertIn("new.txt", [row.path.name for row in next_revision.rows])

    def test_doc_summaries_are_computed_only_for_emitted_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for index in range(5):
                (root / f"file_{index}.py").write_text("# summary\n", encoding="utf-8")
            with mock.patch(
                "lazyviewer.preview.service.cached_top_file_doc_summary",
                return_value="summary",
            ) as summaries:
                document = PreviewService().load(
                    _request(root, directory_max_entries=2)
                )

            self.assertEqual(summaries.call_count, 2)
            self.assertEqual(len(document.rows), 2)

    @unittest.skipIf(shutil.which("git") is None, "git is required")
    def test_loads_modified_tracked_file_as_semantic_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)
            path = root / "demo.py"
            path.write_text("a = 1\nb = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
            path.write_text("a = 1\nb = 22\n", encoding="utf-8")

            document = PreviewService().load(_request(path))

            self.assertIsInstance(document, DiffDocument)
            self.assertEqual(
                [(line.kind, line.text) for line in document.lines],
                [
                    ("context", "a = 1"),
                    ("removed", "b = 2"),
                    ("added", "b = 22"),
                ],
            )


if __name__ == "__main__":
    unittest.main()
