"""Tests for typed root session transitions and declared effects."""

from __future__ import annotations

import unittest
from pathlib import Path

from lazyviewer.preview import PreviewRequest, TextDocument
from lazyviewer.session import (
    ApplyPreview,
    ClockTicked,
    LayoutState,
    PaneWidthAdjusted,
    PreviewCompleted,
    PreviewViewState,
    ReflowPreview,
    SavePaneWidth,
    SessionState,
    SynchronizeWorkspace,
    TerminalResized,
    WorkspaceObserved,
    WorkspaceViewState,
    update,
)
from lazyviewer.tree_model import TreeEntry
from lazyviewer.workspace import (
    WorkspaceDelta,
    WorkspaceQuery,
    WorkspaceRevision,
    WorkspaceSnapshot,
)


def _session() -> SessionState:
    root = Path("/tmp/project").resolve()
    query = WorkspaceQuery.create(
        [root],
        [{root}],
        show_hidden=False,
        skip_gitignored=True,
    )
    snapshot = WorkspaceSnapshot(query, (), WorkspaceRevision("tree", "git"))
    return SessionState(
        workspace=WorkspaceViewState(
            current_path=root / "demo.py",
            active_root=root,
            expanded={root},
            show_hidden=False,
            entries=[TreeEntry(root / "demo.py", 1, False)],
            selected=0,
            snapshot=snapshot,
        ),
        preview=PreviewViewState(rendered="", lines=[]),
        layout=LayoutState(30, 89, 23, 89),
    )


class SessionUpdateTests(unittest.TestCase):
    def test_pane_adjustment_declares_persistence_and_reflow(self) -> None:
        state = _session()
        effects = update(state, PaneWidthAdjusted(columns=120, delta=2))
        self.assertEqual(state.layout.left_width, 32)
        self.assertEqual(
            effects,
            (SavePaneWidth(120, 32), ReflowPreview(120)),
        )

    def test_terminal_resize_updates_layout_before_declaring_reflow(self) -> None:
        state = _session()
        effects = update(state, TerminalResized(columns=100, lines=30))
        self.assertEqual(state.layout.usable_rows, 29)
        self.assertEqual(state.layout.right_width, 69)
        self.assertEqual(effects, (ReflowPreview(100),))

    def test_clock_expires_status_without_external_effect(self) -> None:
        state = _session()
        state.interface.status_message = "done"
        state.interface.status_message_until = 4.0
        self.assertEqual(update(state, ClockTicked(5.0)), ())
        self.assertEqual(state.interface.status_message, "")

    def test_workspace_tree_delta_declares_synchronization(self) -> None:
        state = _session()
        snapshot = state.workspace.snapshot
        assert snapshot is not None
        delta = WorkspaceDelta(snapshot, (0,), ())
        effects = update(state, WorkspaceObserved(delta))
        self.assertEqual(
            effects,
            (SynchronizeWorkspace(state.workspace.current_path),),
        )

    def test_preview_completion_rejects_stale_revision(self) -> None:
        state = _session()
        path = state.workspace.current_path
        stale = PreviewRequest.create(
            path,
            workspace_revision="stale",
            show_hidden=False,
            skip_gitignored=True,
        )
        document = TextDocument(path, "hello", 5)
        self.assertEqual(update(state, PreviewCompleted(stale, document, True)), ())

        current = PreviewRequest.create(
            path,
            workspace_revision="tree:git",
            show_hidden=False,
            skip_gitignored=True,
        )
        self.assertEqual(
            update(state, PreviewCompleted(current, document, True)),
            (ApplyPreview(current, document, True),),
        )


if __name__ == "__main__":
    unittest.main()
