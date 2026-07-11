"""Typed external events accepted by the session update coordinator."""

from __future__ import annotations

from dataclasses import dataclass

from ..preview import PreviewDocument, PreviewRequest
from ..workspace import WorkspaceDelta


@dataclass(frozen=True, slots=True)
class ClockTicked:
    now: float


@dataclass(frozen=True, slots=True)
class TerminalResized:
    columns: int
    lines: int


@dataclass(frozen=True, slots=True)
class PaneWidthAdjusted:
    columns: int
    delta: int


@dataclass(frozen=True, slots=True)
class WorkspaceObserved:
    delta: WorkspaceDelta


@dataclass(frozen=True, slots=True)
class PreviewCompleted:
    request: PreviewRequest
    document: PreviewDocument
    reset_scroll: bool


SessionAction = (
    ClockTicked
    | TerminalResized
    | PaneWidthAdjusted
    | WorkspaceObserved
    | PreviewCompleted
)


__all__ = [
    "ClockTicked",
    "PaneWidthAdjusted",
    "PreviewCompleted",
    "SessionAction",
    "TerminalResized",
    "WorkspaceObserved",
]
