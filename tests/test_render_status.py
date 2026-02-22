from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.render import build_status_line, render_dual_page


class RenderStatusTests(unittest.TestCase):
    def test_build_status_line_places_help_on_right(self) -> None:
        line = build_status_line("file.py (1-10/200  4.5%)", width=60)
        self.assertEqual(len(line), 59)
        self.assertIn(" │ ? Help", line)
        self.assertTrue(line.endswith("│ ? Help"))

    def test_render_status_ends_with_help_suffix(self) -> None:
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
                max_lines=3,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=80,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        matches = re.findall(r"\x1b\[7m([^\x1b]*)\x1b\[0m", rendered)
        self.assertTrue(matches)
        self.assertTrue(matches[-1].endswith("│ ? Help"))

    def test_tree_filter_renders_query_row_in_left_pane(self) -> None:
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
                max_lines=3,
                current_path=Path("/tmp/demo.py"),
                tree_root=Path("/tmp"),
                expanded=set(),
                width=80,
                left_width=30,
                text_x=0,
                wrap_text=False,
                browser_visible=True,
                show_hidden=False,
                tree_filter_active=True,
                tree_filter_query="main",
                tree_filter_editing=True,
            )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("p> main", rendered)


if __name__ == "__main__":
    unittest.main()
