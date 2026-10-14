"""Tests for initial AppState bootstrap behavior."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from lazyviewer.runtime.app_bootstrap import AppStateBootstrap
from lazyviewer.source_pane import SourcePane
from lazyviewer.tree_model import TreeEntry


class AppStateBootstrapTests(unittest.TestCase):
    def test_first_directory_preview_fills_viewport_before_prefetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            captured: dict[str, int] = {}
            for index in range(60):
                (root / f"file_{index:03d}.txt").write_text("preview\n")

            def build_rendered_for_path(*args, **kwargs):
                captured["dir_max_entries"] = int(kwargs["dir_max_entries"])
                return SourcePane.build_rendered_for_path(*args, **kwargs)

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

            self.assertEqual(captured["dir_max_entries"], 27)
            self.assertEqual(state.dir_preview_max_entries, 27)
            self.assertEqual(len(state.lines), state.usable)
            self.assertIn("file_026.txt", state.lines[-1])
            self.assertTrue(state.dir_preview_truncated)

    def test_build_state_initializes_multiple_workspace_roots_from_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            first = root / "one"
            second = root / "two"
            first.mkdir()
            second.mkdir()
            first_file = first / "main.py"
            first_file.write_text("print('one')\n", encoding="utf-8")
            seen_roots: list[tuple[Path, Path | None, int | None]] = []

            def build_tree_entries(tree_root, _expanded, _show_hidden, **kwargs):
                workspace_root = kwargs.get("workspace_root")
                workspace_section = kwargs.get("workspace_section")
                seen_roots.append((tree_root.resolve(), workspace_root, workspace_section))
                return [
                    TreeEntry(
                        path=tree_root.resolve(),
                        depth=0,
                        is_dir=True,
                        workspace_root=workspace_root,
                        workspace_section=workspace_section,
                    )
                ]

            bootstrap = AppStateBootstrap(
                skip_gitignored_for_hidden_mode=lambda show_hidden: not show_hidden,
                load_show_hidden=lambda: False,
                load_named_marks=lambda: {},
                load_left_pane_percent=lambda: None,
                compute_left_width=lambda cols: max(20, cols // 3),
                clamp_left_width=lambda _total, left: left,
                build_tree_entries=build_tree_entries,
                build_rendered_for_path=lambda *_args, **_kwargs: SimpleNamespace(
                    text="",
                    truncated=False,
                    is_directory=False,
                    image_path=None,
                    image_format=None,
                    is_git_diff_preview=False,
                ),
                git_features_default_enabled=True,
                tree_size_labels_default_enabled=True,
                dir_preview_initial_max_entries=100,
            )

            with mock.patch(
                "lazyviewer.runtime.app_bootstrap.shutil.get_terminal_size",
                return_value=os.terminal_size((120, 30)),
            ):
                state = bootstrap.build_state(
                    path=first_file,
                    style="monokai",
                    no_color=True,
                    workspace_paths=[first, second],
                )

            self.assertEqual(state.tree_root, first.resolve())
            self.assertEqual(state.tree_roots, [first.resolve(), second.resolve()])
            self.assertEqual(state.workspace_expanded, [{first.resolve()}, {second.resolve()}])
            self.assertEqual(
                [(entry.path.resolve(), entry.workspace_section) for entry in state.tree_entries],
                [(first.resolve(), 0), (second.resolve(), 1)],
            )
            self.assertEqual(
                [(tree_root, workspace_root.resolve() if workspace_root is not None else None, section) for tree_root, workspace_root, section in seen_roots],
                [
                    (first.resolve(), first.resolve(), 0),
                    (second.resolve(), second.resolve(), 1),
                ],
            )


if __name__ == "__main__":
    unittest.main()
