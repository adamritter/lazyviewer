from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from lazyviewer.search import (
    ContentSearchFinished,
    ContentSearchRequest,
    FileSearchRequest,
    SearchService,
)
from lazyviewer.search.content import ContentMatch
from lazyviewer.workspace import WorkspaceFileIndex, WorkspaceQuery, WorkspaceService


def _wait_for_finish(job, timeout: float = 1.0):
    deadline = time.monotonic() + timeout
    updates = []
    while time.monotonic() < deadline:
        updates.extend(job.poll(0.01))
        finished = next((item for item in updates if isinstance(item, ContentSearchFinished)), None)
        if finished is not None:
            return updates, finished.result
    raise AssertionError("content search did not finish")


class SearchServiceTests(unittest.TestCase):
    def test_file_matching_uses_workspace_owned_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            workspace = WorkspaceService(
                build_tree_signature=lambda *_args: "tree",
                resolve_git_context=lambda _root: (None, None),
                build_git_signature=lambda _git_dir: "git",
                collect_git_status=lambda _root: {},
                build_file_index=lambda snapshot: WorkspaceFileIndex(
                    snapshot.revision,
                    ("src/alpha.py", "src/beta.py"),
                    (root / "src/alpha.py", root / "src/beta.py"),
                    (0, 0),
                ),
            )
            snapshot = workspace.snapshot(
                WorkspaceQuery.create(
                    [root], [{root}], show_hidden=False, skip_gitignored=True
                )
            )
            service = SearchService(workspace)
            matches = service.match_files(FileSearchRequest(snapshot, "beta", 10))
            self.assertEqual([match.label for match in matches], ["src/beta.py"])

    def test_content_cache_is_scoped_to_workspace_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target = root / "demo.py"
            target.write_text("needle\n", encoding="utf-8")
            tree_signature = ["tree-1"]
            calls: list[str] = []

            def backend(search_root, query, _show_hidden, **kwargs):
                calls.append(f"{search_root}:{query}")
                match = ContentMatch(target, 1, 1, "needle")
                callback = kwargs.get("on_match")
                if callback is not None:
                    callback(target, match, 1, 1)
                return {target: [match]}, False, None

            workspace = WorkspaceService(
                build_tree_signature=lambda *_args: tree_signature[0],
                resolve_git_context=lambda _root: (None, None),
                build_git_signature=lambda _git_dir: "git",
                collect_git_status=lambda _root: {},
            )
            query = WorkspaceQuery.create(
                [root], [{root}], show_hidden=False, skip_gitignored=True
            )
            first_snapshot = workspace.snapshot(query)
            service = SearchService(workspace, content_backend=backend)

            request = ContentSearchRequest(first_snapshot, "needle")
            _updates, first = _wait_for_finish(service.start_content(request))
            _updates, second = _wait_for_finish(service.start_content(request))
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1)

            tree_signature[0] = "tree-2"
            second_snapshot = workspace.refresh(first_snapshot, query).snapshot
            _wait_for_finish(
                service.start_content(ContentSearchRequest(second_snapshot, "needle"))
            )
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
