"""Sticky-header rendering and symbol-context tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.source_pane.rendering import sticky_symbol_headers_for_position
from lazyviewer.render import render_dual_page

def _sticky_case(
    source: str,
    source_line: int,
    *,
    suffix: str = ".py",
    wrap_text: bool = False,
    content_rows: int = 8,
) -> list[tuple[str, str]]:
    text_lines = source.splitlines()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"case{suffix}"
        path.write_text(source, encoding="utf-8")
        sticky = sticky_symbol_headers_for_position(
            text_lines=text_lines,
            text_start=max(0, source_line - 1),
            content_rows=content_rows,
            current_path=path,
            wrap_text=wrap_text,
            preview_is_git_diff=False,
        )
    return [(entry.kind, entry.name) for entry in sticky]

class RenderStatusStickyTestsPart2(unittest.TestCase):
    def test_render_nested_sticky_headers_transition_smoothly_for_class_and_method(self) -> None:
        source = (
            "class Box:\n"
            "    def run(self):\n"
            "        first = 1\n"
            "        second = 2\n"
            "        third = 3\n"
            "        fourth = 4\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")

            writes_scrolled_1: list[bytes] = []
            writes_scrolled_2: list[bytes] = []

            with mock.patch(
                "lazyviewer.render.os.write",
                side_effect=lambda _fd, data: writes_scrolled_1.append(data) or len(data),
            ):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=1,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=4,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

            with mock.patch(
                "lazyviewer.render.os.write",
                side_effect=lambda _fd, data: writes_scrolled_2.append(data) or len(data),
            ):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=4,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        scrolled_1_rendered = b"".join(writes_scrolled_1).decode("utf-8", errors="replace")
        scrolled_2_rendered = b"".join(writes_scrolled_2).decode("utf-8", errors="replace")

        self.assertRegex(scrolled_1_rendered, r"\x1b\[4mclass Box:")
        self.assertRegex(scrolled_1_rendered, r"\x1b\[4m\s+def run\(self\):")
        self.assertIn("second = 2", scrolled_1_rendered)
        self.assertIn("third = 3", scrolled_1_rendered)
        self.assertNotIn("first = 1", scrolled_1_rendered)

        self.assertRegex(scrolled_2_rendered, r"\x1b\[4mclass Box:")
        self.assertRegex(scrolled_2_rendered, r"\x1b\[4m\s+def run\(self\):")
        self.assertIn("third = 3", scrolled_2_rendered)
        self.assertIn("fourth = 4", scrolled_2_rendered)
        self.assertNotIn("second = 2", scrolled_2_rendered)

    def test_render_keeps_sticky_header_on_blank_line_inside_function(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "def run():\n"
            "    first = 1\n"
            "\n"
            "    second = 2\n"
            "    return first + second\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=5,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("def run():", rendered)
        self.assertIn("─", rendered)
        self.assertIn("second = 2", rendered)

    def test_render_does_not_show_sticky_header_on_blank_separator_line(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "def first():\n"
            "    return 1\n"
            "\n"
            "def second():\n"
            "    return 2\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=5,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=100,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertNotIn("def first():", rendered)
        self.assertNotIn("─", rendered)
        self.assertIn("def second():", rendered)

    def test_sticky_case_helper_drops_finished_function_before_top_level_for(self) -> None:
        source = (
            "def run():\n"
            "    value = 1\n"
            "\n"
            "for item in items:\n"
            "    print(item)\n"
        )
        self.assertEqual(_sticky_case(source, 4), [])
        self.assertEqual(_sticky_case(source, 5), [])

    def test_sticky_case_helper_shows_function_on_first_body_line(self) -> None:
        source = (
            "def run():\n"
            "    first = 1\n"
            "    second = 2\n"
        )
        self.assertEqual(_sticky_case(source, 2), [("fn", "run")])
