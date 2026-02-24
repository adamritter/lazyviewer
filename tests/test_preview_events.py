"""Tests for preview-pane click/path resolution helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.source_pane.events import directory_preview_target_for_display_line
from lazyviewer.runtime.state import AppState
from lazyviewer.tree_pane.model import TreeEntry


def _build_state_for_rendered_directory(root: Path, rendered: str) -> AppState:
    resolved_root = root.resolve()
    return AppState(
        current_path=resolved_root,
        tree_root=resolved_root,
        expanded={resolved_root},
        show_hidden=False,
        tree_entries=[TreeEntry(path=resolved_root, depth=0, is_dir=True)],
        selected_idx=0,
        rendered=rendered,
        lines=rendered.splitlines(),
        start=0,
        tree_start=0,
        text_x=0,
        wrap_text=False,
        left_width=24,
        right_width=80,
        usable=24,
        max_start=0,
        last_right_width=80,
        dir_preview_path=resolved_root,
    )


class PreviewEventsTests(unittest.TestCase):
    def test_directory_preview_target_parses_rows_with_size_labels_and_git_badges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "src"
            src.mkdir()
            target = src / "main.py"
            target.write_bytes(b"x" * (10 * 1024))

            rendered = "\n".join(
                [
                    f"\033[1;34m{root}/\033[0m",
                    "",
                    "\033[2;38;5;245m\u2514\u2500 \033[0m\033[1;34msrc/\033[0m",
                    "\033[2;38;5;245m   \u2514\u2500 \033[0m\033[38;5;252mmain.py\033[0m\033[38;5;109m [10 KB]\033[0m \033[38;5;214m[M]\033[0m",
                ]
            )
            state = _build_state_for_rendered_directory(root, rendered)

            self.assertEqual(directory_preview_target_for_display_line(state, 2), src.resolve())
            self.assertEqual(directory_preview_target_for_display_line(state, 3), target.resolve())


if __name__ == "__main__":
    unittest.main()
