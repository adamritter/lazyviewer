from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.workspace import WorkspaceFileIndex, WorkspaceQuery, WorkspaceService


class WorkspaceServiceTests(unittest.TestCase):
    def test_refresh_observes_every_root_and_merges_git_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            first = base / "first"
            second = base / "second"
            first.mkdir()
            second.mkdir()

            tree_signatures = {first: "tree-1", second: "tree-2"}
            git_signatures = {first: "git-1", second: "git-2"}
            status_calls: list[Path] = []

            def tree_signature(root: Path, _expanded: set[Path], _show_hidden: bool) -> str:
                return tree_signatures[root]

            def git_context(root: Path) -> tuple[Path, Path]:
                return root, root / ".git"

            def git_signature(git_dir: Path | None) -> str:
                assert git_dir is not None
                return git_signatures[git_dir.parent]

            def git_status(root: Path) -> dict[Path, int]:
                status_calls.append(root)
                shared = base / "shared.py"
                return {shared: 1 if root == first else 2, root / "own.py": 1}

            service = WorkspaceService(
                build_tree_signature=tree_signature,
                resolve_git_context=git_context,
                build_git_signature=git_signature,
                collect_git_status=git_status,
            )
            query = WorkspaceQuery.create(
                [first, second],
                [{first}, {second}],
                show_hidden=False,
                skip_gitignored=True,
            )

            initial = service.snapshot(query)
            self.assertEqual(status_calls, [first, second])
            self.assertEqual(initial.merged_git_status()[base / "shared.py"], 3)

            tree_signatures[second] = "tree-2b"
            tree_delta = service.refresh(initial, query)
            self.assertEqual(tree_delta.tree_changed_sections, (1,))
            self.assertEqual(tree_delta.git_changed_sections, ())
            self.assertEqual(status_calls, [first, second])

            git_signatures[first] = "git-1b"
            git_delta = service.refresh(tree_delta.snapshot, query)
            self.assertEqual(git_delta.tree_changed_sections, ())
            self.assertEqual(git_delta.git_changed_sections, (0,))
            self.assertEqual(status_calls, [first, second, first])

    def test_file_index_cache_is_owned_and_keyed_by_tree_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            tree_signature_value = ["tree-1"]
            builds: list[str] = []

            def build_index(snapshot) -> WorkspaceFileIndex:
                builds.append(snapshot.revision.tree)
                return WorkspaceFileIndex(
                    revision=snapshot.revision,
                    labels=("demo.py",),
                    paths=(root / "demo.py",),
                    sections=(0,),
                )

            service = WorkspaceService(
                build_tree_signature=lambda *_args: tree_signature_value[0],
                resolve_git_context=lambda _root: (None, None),
                build_git_signature=lambda _git_dir: "git",
                collect_git_status=lambda _root: {},
                build_file_index=build_index,
            )
            query = WorkspaceQuery.create(
                [root],
                [{root}],
                show_hidden=False,
                skip_gitignored=True,
            )
            initial = service.snapshot(query)

            first = service.file_index(initial)
            second = service.file_index(initial)
            self.assertIs(first, second)
            self.assertEqual(len(builds), 1)

            tree_signature_value[0] = "tree-2"
            refreshed = service.refresh(initial, query).snapshot
            service.file_index(refreshed)
            self.assertEqual(len(builds), 2)
            self.assertNotEqual(initial.revision.tree, refreshed.revision.tree)

    def test_git_only_refresh_preserves_tree_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            tree_calls = 0
            git_signature = ["git-1"]

            def observe_tree(*_args) -> str:
                nonlocal tree_calls
                tree_calls += 1
                return "tree-1"

            service = WorkspaceService(
                build_tree_signature=observe_tree,
                resolve_git_context=lambda _root: (root, root / ".git"),
                build_git_signature=lambda _git_dir: git_signature[0],
                collect_git_status=lambda _root: {},
            )
            query = WorkspaceQuery.create(
                [root],
                [{root}],
                show_hidden=True,
                skip_gitignored=False,
            )
            initial = service.snapshot(query)
            git_signature[0] = "git-2"

            delta = service.refresh_git(initial, query)

            self.assertEqual(tree_calls, 1)
            self.assertEqual(delta.tree_changed_sections, ())
            self.assertEqual(delta.git_changed_sections, (0,))
            self.assertEqual(delta.snapshot.revision.tree, initial.revision.tree)

    def test_query_constrains_expanded_paths_to_their_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            inside = root / "inside"
            inside.mkdir()
            outside = root.parent
            query = WorkspaceQuery.create(
                [root],
                [{inside, outside}],
                show_hidden=False,
                skip_gitignored=True,
            )
            self.assertEqual(query.expanded_by_root, (frozenset({root, inside}),))


if __name__ == "__main__":
    unittest.main()
