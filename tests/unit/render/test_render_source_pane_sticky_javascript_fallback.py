"""Sticky-header rendering and symbol-context tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.source_pane.rendering import sticky_symbol_headers_for_position
from lazyviewer.source_pane.symbols import MISSING_PARSER_ERROR
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

class RenderStatusStickyTestsPart4(unittest.TestCase):
    def test_render_sticky_headers_work_for_javascript_without_tree_sitter(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "class Box {\n"
            "  constructor() {}\n"
            "}\n"
            "\n"
            "function boot() {\n"
            "  const value = 3;\n"
            "  return value;\n"
            "}\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.js"
            path.write_text(source, encoding="utf-8")
            with (
                mock.patch(
                    "lazyviewer.source_pane.symbols._load_parser",
                    return_value=(None, MISSING_PARSER_ERROR),
                ),
                mock.patch("lazyviewer.render.os.write", side_effect=capture),
            ):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=5,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=6,
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
        self.assertIn("function boot() {", rendered)
        self.assertNotIn("fn boot", rendered)
        self.assertNotIn("class Box", rendered)
        self.assertIn("return value;", rendered)
