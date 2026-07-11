"""Typed side effects requested by pure session coordination decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..preview import PreviewDocument, PreviewRequest


@dataclass(frozen=True, slots=True)
class ReflowPreview:
    columns: int


@dataclass(frozen=True, slots=True)
class SavePaneWidth:
    columns: int
    left_width: int


@dataclass(frozen=True, slots=True)
class SynchronizeWorkspace:
    preferred_path: Path


@dataclass(frozen=True, slots=True)
class RefreshPreview:
    reset_scroll: bool = False
    reset_directory_budget: bool = False


@dataclass(frozen=True, slots=True)
class ApplyPreview:
    request: PreviewRequest
    document: PreviewDocument
    reset_scroll: bool


SessionEffect = (
    ReflowPreview
    | SavePaneWidth
    | SynchronizeWorkspace
    | RefreshPreview
    | ApplyPreview
)


__all__ = [
    "ApplyPreview",
    "RefreshPreview",
    "ReflowPreview",
    "SavePaneWidth",
    "SessionEffect",
    "SynchronizeWorkspace",
]
