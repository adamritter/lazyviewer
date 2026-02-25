"""Workspace-root helper tests for normalization and display labels."""

from __future__ import annotations

import unittest
from pathlib import Path

from lazyviewer.tree_pane.workspace_roots import (
    normalized_workspace_roots,
    workspace_root_display_labels,
)


class WorkspaceRootHelpersTests(unittest.TestCase):
    def test_normalized_workspace_roots_preserves_duplicates_and_keeps_active(self) -> None:
        root = Path("/tmp/project").resolve()
        nested = (root / "nested").resolve()

        roots = normalized_workspace_roots([root, root, nested], nested)

        self.assertEqual(roots, [root, root, nested])

    def test_workspace_root_display_labels_show_common_parent_context(self) -> None:
        root = Path("/tmp/project").resolve()
        nested = (root / "nested").resolve()

        labels = workspace_root_display_labels([root, nested], nested)

        self.assertEqual(labels, ["project", "project/nested"])

    def test_workspace_root_display_labels_preserve_distinguishing_prefix(self) -> None:
        first = Path("/tmp/workspace-alpha").resolve()
        second = Path("/tmp/workspace-beta").resolve()

        labels = workspace_root_display_labels([first, second], first)

        self.assertEqual(labels, ["tmp/workspace-alpha", "tmp/workspace-beta"])


if __name__ == "__main__":
    unittest.main()
