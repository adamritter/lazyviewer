from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.workspace import collect_root_file_labels


class WorkspaceIndexTests(unittest.TestCase):
    def test_hidden_policy_is_applied_by_workspace_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / ".dotfile").write_text("hidden", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "b.py").write_text("b", encoding="utf-8")
            (root / "src" / ".secret.py").write_text("secret", encoding="utf-8")
            with mock.patch("lazyviewer.workspace.index.shutil.which", return_value=None):
                hidden_off = collect_root_file_labels(root, False, False)
                hidden_on = collect_root_file_labels(root, True, False)
            self.assertEqual(hidden_off, ["a.txt", "src/b.py"])
            self.assertIn(".dotfile", hidden_on)
            self.assertIn("src/.secret.py", hidden_on)

    def test_catalog_prefers_rg_and_returns_sorted_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("x", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")
            completed = mock.Mock(stdout="src/main.py\na.txt\n")
            with mock.patch("lazyviewer.workspace.index.shutil.which", return_value="rg"), mock.patch(
                "lazyviewer.workspace.index.subprocess.run", return_value=completed
            ) as run:
                labels = collect_root_file_labels(root, False, False)
            self.assertEqual(labels, ["a.txt", "src/main.py"])
            run.assert_called_once()

    def test_catalog_falls_back_to_walk_when_rg_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("x", encoding="utf-8")
            with mock.patch("lazyviewer.workspace.index.shutil.which", return_value="rg"), mock.patch(
                "lazyviewer.workspace.index.subprocess.run",
                side_effect=subprocess.SubprocessError("failed"),
            ):
                labels = collect_root_file_labels(root, False, False)
            self.assertEqual(labels, ["src/main.py"])


if __name__ == "__main__":
    unittest.main()
