"""Tests for initial AppState bootstrap behavior."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from lazyviewer.runtime.app_bootstrap import AppStateBootstrap
from lazyviewer.tree_model import TreeEntry


class AppStateBootstrapTests(unittest.TestCase):
    def test_build_state_limits_initial_directory_preview_entries_to_viewport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            captured: dict[str, int] = {}

            def build_rendered_for_path(*_args, **kwargs):
                captured["dir_max_entries"] = int(kwargs["dir_max_entries"])
                return SimpleNamespace(
                    text=f"{root}/\n",
                    truncated=True,
                    is_directory=True,
                    image_path=None,
                    image_format=None,
                    is_git_diff_preview=False,
                )

            bootstrap = AppStateBootstrap(
                skip_gitignored_for_hidden_mode=lambda show_hidden: not show_hidden,
                load_show_hidden=lambda: False,
                load_named_marks=lambda: {},
                load_left_pane_percent=lambda: None,
                compute_left_width=lambda cols: max(20, cols // 3),
                clamp_left_width=lambda _total, left: left,
                build_tree_entries=lambda tree_root, _expanded, _show_hidden, **_kwargs: [
                    TreeEntry(path=tree_root, depth=0, is_dir=True)
                ],
                build_rendered_for_path=build_rendered_for_path,
                git_features_default_enabled=True,
                tree_size_labels_default_enabled=True,
                dir_preview_initial_max_entries=1_000,
            )

            with mock.patch(
                "lazyviewer.runtime.app_bootstrap.shutil.get_terminal_size",
                return_value=os.terminal_size((120, 30)),
            ):
                state = bootstrap.build_state(path=root, style="monokai", no_color=True)

            self.assertEqual(captured["dir_max_entries"], 25)
            self.assertEqual(state.dir_preview_max_entries, 25)


if __name__ == "__main__":
    unittest.main()
