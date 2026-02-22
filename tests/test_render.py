from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.render import render_dual_page


class RenderBehaviorTests(unittest.TestCase):
    def _render_capture(self, **kwargs) -> str:
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
                width=120,
                left_width=40,
                text_x=0,
                wrap_text=False,
                browser_visible=False,
                show_hidden=False,
                **kwargs,
            )
        return b"".join(writes).decode("utf-8", errors="replace")

    def test_status_shows_picker_shortcuts_when_picker_inactive(self) -> None:
        rendered = self._render_capture(picker_active=False)
        self.assertIn("[Ctrl+P files]", rendered)
        self.assertIn("[s symbols]", rendered)

    def test_status_shows_picker_hint_when_picker_active(self) -> None:
        rendered = self._render_capture(
            picker_active=True,
            picker_focus="query",
            picker_mode="files",
        )
        self.assertIn("[Picker: type query, Enter/Tab -> tree, Esc close]", rendered)


if __name__ == "__main__":
    unittest.main()
