"""Help-panel and help-modal rendering tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.render import render_dual_page, render_help_page


class RenderStatusHelpTests(unittest.TestCase):
    def test_bottom_help_panel_splits_tree_and_text_sections_when_browser_visible(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_dual_page(
                text_lines=["line 1", "line 2"],
                text_start=0,
                tree_entries=[],
                tree_start=0,
                tree_selected=0,
                max_lines=7,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=120,
                left_width=40,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                show_help=True,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("TREE", rendered)
        self.assertIn("TEXT + EXTRAS", rendered)
        self.assertIn("\033[38;5;229mh/j/k/l\033[0m", rendered)
        self.assertIn("\033[38;5;229mr\033[0m", rendered)

    def test_help_modal_mentions_reroot_key(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        with mock.patch("lazyviewer.render.os.write", side_effect=capture):
            render_help_page(width=120, height=32)

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("\033[38;5;229mr\033[0m", rendered)
        self.assertIn("\033[38;5;229mR\033[0m", rendered)
        self.assertIn("\033[38;5;229mn/N/p\033[0m", rendered)
        self.assertIn("Alt+Left/Right", rendered)
        self.assertIn("m{key}", rendered)
        self.assertIn("'{key}", rendered)
        self.assertIn("command palette", rendered)
        self.assertIn("Ctrl+U", rendered)
        self.assertIn("Ctrl+D", rendered)
        self.assertIn("tree root -> parent directory", rendered)
        self.assertIn("tree root -> selected directory", rendered)

