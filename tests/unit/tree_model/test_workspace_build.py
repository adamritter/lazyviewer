"""Workspace-tree build tests for multi-root tree pane rendering."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.tree_model import build_workspace_tree_entries


class WorkspaceTreeBuildTests(unittest.TestCase):
    def test_build_workspace_tree_entries_contains_each_root_as_top_level_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            (nested / "demo.py").write_text("print('x')\n", encoding="utf-8")

            entries = build_workspace_tree_entries(
                [root, nested],
                active_root=nested,
                expanded={nested},
                expanded_by_root=None,
                show_hidden=False,
            )

            top_level_dirs = [entry.path.resolve() for entry in entries if entry.is_dir and entry.depth == 0]
            self.assertEqual(top_level_dirs, [root, nested])
            self.assertIn(nested / "demo.py", [entry.path.resolve() for entry in entries if not entry.is_dir])


if __name__ == "__main__":
    unittest.main()
