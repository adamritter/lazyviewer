"""Correctness and isolation tests for retained pane rendering."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import re
import unittest

from lazyviewer.render import RenderContext, RetainedPageRenderer
from lazyviewer.render.ansi import ANSI_ESCAPE_RE
from lazyviewer.render.diff import diff_frames
from lazyviewer.render.frame import Frame, Rect, Surface
from lazyviewer.tree_model import TreeEntry


CURSOR_RE = re.compile(rb"\x1b\[(\d+);(\d+)H")


def _context() -> RenderContext:
    root = Path("/tmp/retained-render-project")
    return RenderContext(
        text_lines=[f"preview line {index}\n" for index in range(30)],
        text_start=0,
        tree_entries=[
            TreeEntry(path=root, depth=0, is_dir=True),
            TreeEntry(path=root / "alpha.py", depth=1, is_dir=False),
            TreeEntry(path=root / "beta.py", depth=1, is_dir=False),
        ],
        tree_start=0,
        tree_selected=1,
        max_lines=6,
        current_path=root / "alpha.py",
        tree_root=root,
        expanded={root},
        width=80,
        left_width=24,
        text_x=0,
        wrap_text=False,
        browser_visible=True,
        show_hidden=False,
        tree_roots=[root],
        workspace_expanded=[{root}],
    )


def _surface(frame: Frame, name: str) -> Surface:
    return next(surface for surface in frame.surfaces if surface.name == name)


def _plain_row(text: str, width: int) -> str:
    plain = ANSI_ESCAPE_RE.sub("", text)
    return (plain[:width] + (" " * width))[:width]


def _plain_grid(frame: Frame) -> list[list[str]]:
    grid = [[" " for _ in range(frame.width)] for _ in range(frame.height)]
    for surface in frame.surfaces:
        for row_index, row in enumerate(surface.rows):
            plain = _plain_row(row, surface.rect.width)
            y = surface.rect.y + row_index
            for offset, char in enumerate(plain):
                grid[y][surface.rect.x + offset] = char
    return grid


def _apply_patch(grid: list[list[str]], data: bytes) -> None:
    matches = list(CURSOR_RE.finditer(data))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(data)
        payload = data[match.end() : end].decode("utf-8")
        clears_to_edge = "\x1b[K" in payload
        plain = ANSI_ESCAPE_RE.sub("", payload)
        y = int(match.group(1)) - 1
        x = int(match.group(2)) - 1
        for offset, char in enumerate(plain):
            if x + offset < len(grid[y]):
                grid[y][x + offset] = char
        if clears_to_edge:
            for column in range(x + len(plain), len(grid[y])):
                grid[y][column] = " "


class RetainedRenderingTests(unittest.TestCase):
    def test_preview_scroll_reuses_tree_presentation(self) -> None:
        context = _context()
        renderer = RetainedPageRenderer()
        first = renderer.render(context)
        second = renderer.render(replace(context, text_start=1))

        self.assertEqual(renderer.stats.tree_renders, 1)
        self.assertEqual(renderer.stats.preview_renders, 2)
        self.assertEqual(renderer.stats.help_renders, 1)
        self.assertIs(_surface(first, "tree").rows, _surface(second, "tree").rows)

    def test_tree_selection_change_reuses_preview_presentation(self) -> None:
        context = _context()
        renderer = RetainedPageRenderer()
        first = renderer.render(context)
        second = renderer.render(replace(context, tree_selected=2))

        self.assertEqual(renderer.stats.tree_renders, 2)
        self.assertEqual(renderer.stats.preview_renders, 1)
        self.assertIs(_surface(first, "preview").rows, _surface(second, "preview").rows)

    def test_preview_scroll_patch_does_not_touch_tree_content_rows(self) -> None:
        context = _context()
        renderer = RetainedPageRenderer()
        first = renderer.render(context)
        second = renderer.render(replace(context, text_start=1))

        patch = diff_frames(first, second)
        cursor_positions = [
            (int(match.group(1)), int(match.group(2)))
            for match in CURSOR_RE.finditer(patch.data)
        ]

        self.assertFalse(patch.full_repaint)
        self.assertNotIn(b"\x1b[J", patch.data)
        self.assertLess(len(patch.data), len(second.data))
        self.assertTrue(cursor_positions)
        for row, column in cursor_positions:
            if row <= context.max_lines:
                self.assertEqual(column, context.left_width + 2)
            else:
                self.assertEqual(column, 1)  # The full-width status row.

    def test_incremental_patch_reconstructs_full_logical_screen(self) -> None:
        context = _context()
        renderer = RetainedPageRenderer()
        first = renderer.render(context)
        second = renderer.render(replace(context, text_start=3))
        patch = diff_frames(first, second)

        actual = _plain_grid(first)
        _apply_patch(actual, patch.data)

        self.assertEqual(actual, _plain_grid(second))

    def test_unchanged_frame_emits_no_bytes(self) -> None:
        frame = RetainedPageRenderer().render(_context())

        patch = diff_frames(frame, frame)

        self.assertEqual(patch.data, b"")
        self.assertEqual(patch.rows_touched, 0)
        self.assertEqual(patch.surfaces_touched, 0)
        self.assertFalse(patch.full_repaint)

    def test_changed_row_is_style_isolated_and_padded_to_surface_width(self) -> None:
        rect = Rect(3, 0, 6, 1)
        first = Frame(
            "full-one",
            width=10,
            height=1,
            surfaces=(Surface("pane", rect, ("abcdef",)),),
        )
        second = Frame(
            "full-two",
            width=10,
            height=1,
            surfaces=(Surface("pane", rect, ("\x1b[31mx",)),),
        )

        patch = diff_frames(first, second)

        self.assertEqual(
            patch.data,
            b"\x1b[1;4H\x1b[0m\x1b[31mx\x1b[0m     ",
        )

    def test_geometry_change_forces_canonical_full_repaint(self) -> None:
        context = _context()
        renderer = RetainedPageRenderer()
        first = renderer.render(context)
        resized = renderer.render(replace(context, width=90))

        patch = diff_frames(first, resized)

        self.assertTrue(patch.full_repaint)
        self.assertEqual(patch.data, resized.data)

    def test_runtime_mode_builds_full_repaint_only_when_differ_requests_it(self) -> None:
        frame = RetainedPageRenderer(canonical_text=False).render(_context())

        patch = diff_frames(None, frame)

        self.assertEqual(frame.text, "")
        self.assertTrue(patch.full_repaint)
        self.assertTrue(patch.data.startswith(b"\x1b[H\x1b[J"))
        self.assertIn(b"preview line 0", patch.data)


if __name__ == "__main__":
    unittest.main()
