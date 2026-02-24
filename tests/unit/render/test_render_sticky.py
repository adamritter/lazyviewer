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


class RenderStatusStickyTests(unittest.TestCase):
    def test_render_shows_sticky_symbol_headers_with_separator_lines(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "class Demo:\n"
            "    def run(self):\n"
            "        x = 1\n"
            "        y = 2\n"
            "        return x + y\n"
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
                    max_lines=3,
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
        self.assertRegex(rendered, r"\x1b\[4mclass Demo:")
        self.assertRegex(rendered, r"\x1b\[4m\s+def run\(self\):")
        self.assertNotIn("fn run", rendered)
        self.assertIn("─", rendered)
        self.assertIn("return x + y", rendered)

    def test_render_shows_full_nested_sticky_chain(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def run(self):\n"
            "            value = 2\n"
            "            return value\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=4,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=7,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=120,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        outer_idx = rendered.find("class Outer:")
        inner_idx = rendered.find("class Inner:")
        run_idx = rendered.find("def run(self):")
        self.assertGreaterEqual(outer_idx, 0)
        self.assertGreater(inner_idx, outer_idx)
        self.assertGreater(run_idx, inner_idx)

    def test_render_shows_sticky_headers_for_git_diff_preview(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source = (
            "class Box:\n"
            "    def first(self):\n"
            "        pass\n"
            "\n"
            "    def second(self):\n"
            "        value = 2\n"
            "        return value\n"
        )
        # Simulate annotated diff preview lines where a removed line is injected.
        text_lines = [
            "class Box:",
            "    def first(self):",
            "\033[48;2;92;43;49m        removed = 0\033[K\033[0m",
            "\033[48;2;36;74;52m        pass\033[K\033[0m",
            "",
            "    def second(self):",
            "        value = 2",
            "        return value",
        ]

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch("lazyviewer.render.os.write", side_effect=capture):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=4,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=4,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=120,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                    preview_is_git_diff=True,
                )

        rendered = b"".join(writes).decode("utf-8", errors="replace")
        self.assertRegex(rendered, r"\x1b\[4mclass Box:")
        self.assertNotRegex(rendered, r"\x1b\[4m\s+def second\(self\):")
        self.assertIn("def second(self):", rendered)

    def test_git_diff_sticky_render_avoids_quadratic_diff_mapping_calls(self) -> None:
        writes: list[bytes] = []

        def capture(_fd: int, data: bytes) -> int:
            writes.append(data)
            return len(data)

        source_lines = ["class Big:"]
        diff_lines = ["class Big:"]
        for idx in range(1, 351):
            source_lines.extend(
                [
                    f"    def m{idx}(self):",
                    f"        value = {idx}",
                    "        return value",
                ]
            )
            diff_lines.extend(
                [
                    f"    def m{idx}(self):",
                    "\033[48;2;92;43;49m        old_value = 0\033[K\033[0m",
                    f"\033[48;2;36;74;52m        value = {idx}\033[K\033[0m",
                    "        return value",
                ]
            )

        source = "\n".join(source_lines) + "\n"
        text_lines = diff_lines
        diff_lookup_calls = {"count": 0}

        import lazyviewer.source_pane.rendering as preview_rendering_mod

        original_source_line_raw_text = preview_rendering_mod.source_line_raw_text

        def counting_source_line_raw_text(
            text_lines_arg: list[str],
            source_line: int,
            wrap_text: bool,
            preview_is_git_diff: bool = False,
        ) -> str:
            if preview_is_git_diff:
                diff_lookup_calls["count"] += 1
            return original_source_line_raw_text(
                text_lines_arg,
                source_line,
                wrap_text,
                preview_is_git_diff=preview_is_git_diff,
            )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.py"
            path.write_text(source, encoding="utf-8")
            with mock.patch(
                "lazyviewer.source_pane.rendering.source_line_raw_text",
                side_effect=counting_source_line_raw_text,
            ), mock.patch(
                "lazyviewer.render.os.write", side_effect=capture
            ):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=max(0, len(text_lines) - 6),
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=6,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=140,
                    left_width=30,
                    text_x=0,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                    preview_is_git_diff=True,
                )

        # Repeated diff-source remapping used to spike into hundreds/thousands of calls per frame.
        self.assertLess(diff_lookup_calls["count"], 80)

    def test_render_scroll_by_one_keeps_lower_content_progressing_by_one_with_sticky(self) -> None:
        source = (
            "def run():\n"
            "    first = 1\n"
            "    second = 2\n"
            "    third = 3\n"
            "    fourth = 4\n"
        )
        text_lines = source.splitlines()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(source, encoding="utf-8")

            writes_top: list[bytes] = []
            writes_scrolled_1: list[bytes] = []
            writes_scrolled_2: list[bytes] = []

            def capture_top(_fd: int, data: bytes) -> int:
                writes_top.append(data)
                return len(data)

            def capture_scrolled_1(_fd: int, data: bytes) -> int:
                writes_scrolled_1.append(data)
                return len(data)

            def capture_scrolled_2(_fd: int, data: bytes) -> int:
                writes_scrolled_2.append(data)
                return len(data)

            with mock.patch("lazyviewer.render.os.write", side_effect=capture_top):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=0,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=3,
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

            with mock.patch("lazyviewer.render.os.write", side_effect=capture_scrolled_1):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=1,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=3,
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

            with mock.patch("lazyviewer.render.os.write", side_effect=capture_scrolled_2):
                render_dual_page(
                    text_lines=text_lines,
                    text_start=2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=3,
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

        top_rendered = b"".join(writes_top).decode("utf-8", errors="replace")
        scrolled_1_rendered = b"".join(writes_scrolled_1).decode("utf-8", errors="replace")
        scrolled_2_rendered = b"".join(writes_scrolled_2).decode("utf-8", errors="replace")

        self.assertIn("def run():", top_rendered)
        self.assertIn("first = 1", top_rendered)
        self.assertIn("second = 2", top_rendered)

        self.assertRegex(scrolled_1_rendered, r"\x1b\[4mdef run\(\):")
        self.assertIn("second = 2", scrolled_1_rendered)
        self.assertIn("third = 3", scrolled_1_rendered)
        self.assertNotIn("first = 1", scrolled_1_rendered)

        self.assertRegex(scrolled_2_rendered, r"\x1b\[4mdef run\(\):")
        self.assertIn("third = 3", scrolled_2_rendered)
        self.assertIn("fourth = 4", scrolled_2_rendered)
        self.assertNotIn("second = 2", scrolled_2_rendered)

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
                    return_value=(None, "Tree-sitter parser package not found. Install tree-sitter-languages."),
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

