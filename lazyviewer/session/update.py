"""Root state transition function for externally observed session events."""

from __future__ import annotations

from ..tree_model import clamp_left_width
from .actions import (
    ClockTicked,
    PaneWidthAdjusted,
    PreviewCompleted,
    SessionAction,
    TerminalResized,
    WorkspaceObserved,
)
from .effects import (
    ApplyPreview,
    RefreshPreview,
    ReflowPreview,
    SavePaneWidth,
    SessionEffect,
    SynchronizeWorkspace,
)
from .model import SessionState


def update(state: SessionState, action: SessionAction) -> tuple[SessionEffect, ...]:
    """Apply one typed action and return explicit effects for runtime execution."""
    if isinstance(action, ClockTicked):
        if (
            state.interface.status_message
            and action.now >= state.interface.status_message_until
        ):
            state.interface.status_message = ""
            state.interface.status_message_until = 0.0
            state.interface.dirty = True
        return ()

    if isinstance(action, TerminalResized):
        previous_usable = state.layout.usable_rows
        previous_left = state.layout.left_width
        previous_right = state.layout.right_width
        state.layout.usable_rows = max(1, action.lines - 1)
        state.layout.left_width = clamp_left_width(action.columns, state.layout.left_width)
        state.layout.right_width = max(1, action.columns - state.layout.left_width - 1)
        changed = (
            previous_usable != state.layout.usable_rows
            or previous_left != state.layout.left_width
            or previous_right != state.layout.right_width
        )
        if changed:
            state.interface.dirty = True
        if state.layout.right_width != state.layout.last_right_width:
            state.layout.last_right_width = state.layout.right_width
            return (ReflowPreview(action.columns),)
        return ()

    if isinstance(action, PaneWidthAdjusted):
        next_width = clamp_left_width(
            action.columns,
            state.layout.left_width + action.delta,
        )
        if next_width == state.layout.left_width:
            return ()
        state.layout.left_width = next_width
        state.layout.right_width = max(1, action.columns - next_width - 1)
        state.layout.last_right_width = state.layout.right_width
        state.interface.dirty = True
        return (
            SavePaneWidth(action.columns, next_width),
            ReflowPreview(action.columns),
        )

    if isinstance(action, WorkspaceObserved):
        delta = action.delta
        previous_status = state.git.status
        state.workspace.snapshot = delta.snapshot
        state.git.status = delta.snapshot.merged_git_status() if state.git.enabled else {}
        effects: list[SessionEffect] = []
        if delta.tree_changed:
            preferred = (
                state.workspace.entries[state.workspace.selected].path.resolve()
                if state.workspace.entries
                and 0 <= state.workspace.selected < len(state.workspace.entries)
                else state.workspace.current_path.resolve()
            )
            effects.append(SynchronizeWorkspace(preferred))
        elif delta.git_changed or state.git.status != previous_status:
            effects.append(RefreshPreview())
        if delta.tree_changed or delta.git_changed or state.git.status != previous_status:
            state.interface.dirty = True
        return tuple(effects)

    if isinstance(action, PreviewCompleted):
        snapshot = state.workspace.snapshot
        revision = snapshot.revision.value if snapshot is not None else "unversioned"
        if action.request.workspace_revision != revision:
            return ()
        if action.request.target != state.workspace.current_path.resolve():
            return ()
        return (
            ApplyPreview(
                action.request,
                action.document,
                action.reset_scroll,
            ),
        )

    raise TypeError(f"unsupported session action: {type(action)!r}")


__all__ = ["update"]
