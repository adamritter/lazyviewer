"""Tests for initial SessionState bootstrap behavior."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.preview import DirectoryDocument, PreviewService
from lazyviewer.runtime.bootstrap import SessionBootstrap
from lazyviewer.source_pane.presenter import PreviewPresenter
from lazyviewer.tree_model import TreeEntry
from lazyviewer.ui_theme import resolve_theme
from lazyviewer.workspace import WorkspaceService


def _preview_dependencies() -> tuple[WorkspaceService, PreviewService, PreviewPresenter]:
    return (
        WorkspaceService(),
        PreviewService(),
        PreviewPresenter(style="monokai", color=False, theme=resolve_theme(None, no_color=True)),
    )


class SessionBootstrapTests(unittest.TestCase):
    def test_build_state_limits_initial_directory_preview_entries_to_viewport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace, preview_service, presenter = _preview_dependencies()

            bootstrap = SessionBootstrap(
                skip_gitignored_for_hidden_mode=lambda show_hidden: not show_hidden,
                load_show_hidden=lambda: False,
                load_named_marks=lambda: {},
                load_left_pane_percent=lambda: None,
                compute_left_width=lambda cols: max(20, cols // 3),
                clamp_left_width=lambda _total, left: left,
                build_tree_entries=lambda tree_root, _expanded, _show_hidden, **_kwargs: [
                    TreeEntry(path=tree_root, depth=0, is_dir=True)
                ],
                workspace_service=workspace,
                preview_service=preview_service,
                preview_presenter=presenter,
                git_features_default_enabled=True,
                tree_size_labels_default_enabled=True,
                dir_preview_initial_max_entries=1_000,
            )

            with mock.patch(
                "lazyviewer.runtime.bootstrap.shutil.get_terminal_size",
                return_value=os.terminal_size((120, 30)),
            ):
                state = bootstrap.build_state(path=root, no_color=True)

            self.assertIsInstance(state.preview.document, DirectoryDocument)
            self.assertEqual(state.preview.document.max_entries, 25)
            self.assertEqual(state.preview.directory_max_entries, 25)

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

            workspace, preview_service, presenter = _preview_dependencies()

            bootstrap = SessionBootstrap(
                skip_gitignored_for_hidden_mode=lambda show_hidden: not show_hidden,
                load_show_hidden=lambda: False,
                load_named_marks=lambda: {},
                load_left_pane_percent=lambda: None,
                compute_left_width=lambda cols: max(20, cols // 3),
                clamp_left_width=lambda _total, left: left,
                build_tree_entries=build_tree_entries,
                workspace_service=workspace,
                preview_service=preview_service,
                preview_presenter=presenter,
                git_features_default_enabled=True,
                tree_size_labels_default_enabled=True,
                dir_preview_initial_max_entries=100,
            )

            with mock.patch(
                "lazyviewer.runtime.bootstrap.shutil.get_terminal_size",
                return_value=os.terminal_size((120, 30)),
            ):
                state = bootstrap.build_state(
                    path=first_file,
                    no_color=True,
                    workspace_paths=[first, second],
                )

            self.assertEqual(state.workspace.active_root, first.resolve())
            self.assertEqual(state.workspace.roots, [first.resolve(), second.resolve()])
            self.assertEqual(state.workspace.expanded_by_root, [{first.resolve()}, {second.resolve()}])
            self.assertEqual(
                [(entry.path.resolve(), entry.workspace_section) for entry in state.workspace.entries],
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
