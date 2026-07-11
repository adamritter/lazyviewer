"""Terminal presentation tests for semantic preview documents."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.git_status import GIT_STATUS_CHANGED
from lazyviewer.preview import (
    BinaryDocument,
    DiffDocument,
    DiffLine,
    DirectoryDocument,
    DirectoryRow,
    ImageDocument,
    TextDocument,
)
from lazyviewer.source_pane.presenter import PreviewPresenter
from lazyviewer.ui_theme import resolve_theme


def _presenter(*, color: bool) -> PreviewPresenter:
    return PreviewPresenter(
        style="monokai",
        color=color,
        theme=resolve_theme(None, no_color=not color),
    )


class PreviewPresenterTests(unittest.TestCase):
    def test_presents_binary_and_image_notices(self) -> None:
        path = Path("/tmp/data.bin")
        self.assertIn("<binary file: 42 bytes>", _presenter(color=False).present(BinaryDocument(path, 42)).text)
        rendered = _presenter(color=False).present(ImageDocument(path, "png"))
        self.assertEqual(rendered.image_path, path)
        self.assertIn("Kitty graphics protocol", rendered.text)

    def test_large_text_bypasses_syntax_colorization(self) -> None:
        document = TextDocument(Path("big.py"), "x = 1\n", 300_000)
        with mock.patch("lazyviewer.source_pane.presenter.colorize_source") as colorize:
            rendered = _presenter(color=True).present(document)
        self.assertEqual(rendered.text, "x = 1\n")
        colorize.assert_not_called()

    def test_directory_presentation_uses_semantic_rows(self) -> None:
        root = Path("/tmp/project")
        document = DirectoryDocument(
            path=root,
            rows=(
                DirectoryRow(
                    path=root / "large.py",
                    depth=1,
                    is_dir=False,
                    is_last=True,
                    ancestor_last=(),
                    file_size=10 * 1024,
                    git_status_flags=GIT_STATUS_CHANGED,
                    doc_summary="important module",
                ),
            ),
            truncated=True,
            max_entries=1,
        )

        rendered = _presenter(color=False).present(document)

        self.assertIn("large.py [10 KB] [M]  -- important module", rendered.text)
        self.assertIn("... truncated after 1 entries ...", rendered.text)

    def test_plain_diff_has_explicit_markers(self) -> None:
        document = DiffDocument(
            Path("demo.py"),
            (
                DiffLine("context", "a = 1", 1),
                DiffLine("removed", "b = 2", None),
                DiffLine("added", "b = 3", 2),
            ),
        )
        rendered = _presenter(color=False).present(document)
        self.assertEqual(rendered.text.splitlines(), ["  a = 1", "- b = 2", "+ b = 3"])

    def test_colored_diff_uses_backgrounds_without_plain_markers(self) -> None:
        document = DiffDocument(
            Path("demo.py"),
            (DiffLine("added", "value = 2", 1),),
        )
        with mock.patch(
            "lazyviewer.source_pane.presenter.colorize_source",
            return_value="\x1b[34mvalue\x1b[0m = 2",
        ):
            rendered = _presenter(color=True).present(document)
        self.assertIn("\x1b[", rendered.text)
        self.assertIn("48;2;36;74;52", rendered.text)


if __name__ == "__main__":
    unittest.main()
