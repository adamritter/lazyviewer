"""Tests for preview-pane click/path resolution helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.source_pane.interaction.events import directory_preview_target_for_display_line
from lazyviewer.preview import DirectoryDocument, DirectoryRow
from lazyviewer.session import LayoutState, PreviewViewState, SessionState, WorkspaceViewState
from lazyviewer.tree_model import TreeEntry


def _build_state_for_rendered_directory(root: Path, rendered: str) -> SessionState:
    resolved_root = root.resolve()
    document = DirectoryDocument(
        path=resolved_root,
        rows=(
            DirectoryRow(
                path=(resolved_root / "src").resolve(),
                depth=1,
                is_dir=True,
                is_last=True,
                ancestor_last=(),
            ),
            DirectoryRow(
                path=(resolved_root / "src" / "main.py").resolve(),
                depth=2,
                is_dir=False,
                is_last=True,
                ancestor_last=(True,),
                file_size=10 * 1024,
            ),
        ),
        truncated=False,
        max_entries=100,
    )
    return SessionState(
        workspace=WorkspaceViewState(
            current_path=resolved_root, active_root=resolved_root,
            expanded={resolved_root}, show_hidden=False,
            entries=[TreeEntry(path=resolved_root, depth=0, is_dir=True)], selected=0,
        ),
        preview=PreviewViewState(
            rendered=rendered, lines=rendered.splitlines(),
            directory_path=resolved_root, document=document,
        ),
        layout=LayoutState(left_width=24, right_width=80, usable_rows=24, last_right_width=80),
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
