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

class RenderStatusStickyTestsPart3(unittest.TestCase):
    def test_sticky_case_helper_keeps_outer_class_when_previous_method_ends(self) -> None:
        source = (
            "class Box:\n"
            "    def first(self):\n"
            "        return 1\n"
            "\n"
            "    def second(self):\n"
            "        value = 2\n"
            "        return value\n"
        )
        self.assertEqual(_sticky_case(source, 5), [("class", "Box"), ("fn", "second")])
        self.assertEqual(_sticky_case(source, 6), [("class", "Box"), ("fn", "second")])
        self.assertEqual(_sticky_case(source, 7), [("class", "Box"), ("fn", "second")])

    def test_sticky_case_helper_handles_closing_brace_transition(self) -> None:
        source = (
            "function run() {\n"
            "  if (value) {\n"
            "    use(value);\n"
            "  }\n"
            "}\n"
            "\n"
            "for (const item of items) {\n"
            "  use(item);\n"
            "}\n"
        )
        self.assertEqual(_sticky_case(source, 5, suffix=".js"), [("fn", "run")])
        self.assertEqual(_sticky_case(source, 6, suffix=".js"), [])
        self.assertEqual(_sticky_case(source, 7, suffix=".js"), [])

    def test_sticky_case_helper_drops_function_before_decorator_and_next_definition(self) -> None:
        source = (
            "def first():\n"
            "    return 1\n"
            "\n"
            "@decorator\n"
            "def second():\n"
            "    return 2\n"
        )
        self.assertEqual(_sticky_case(source, 4), [])
        self.assertEqual(_sticky_case(source, 5), [])

    def test_sticky_case_helper_drops_function_before_top_level_comment_and_loop(self) -> None:
        source = (
            "def first():\n"
            "    return 1\n"
            "\n"
            "# outside the function now\n"
            "for value in values:\n"
            "    print(value)\n"
        )
        self.assertEqual(_sticky_case(source, 4), [])
        self.assertEqual(_sticky_case(source, 6), [])

    def test_render_sticky_headers_preserve_source_highlighting(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "def run():\n"
            "    return 1\n"
            "    return 2\n"
        )
        text_lines = [
            "\033[1;34mdef\033[39;49;00m run():",
            "    return 1",
            "    return 2",
        ]

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

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertIn("\033[39;49;00;4m run():", rendered)
