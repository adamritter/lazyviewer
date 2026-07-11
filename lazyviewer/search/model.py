"""Typed requests and results for file and content search."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from ..workspace import WorkspaceSnapshot
from .content import ContentMatch


@dataclass(frozen=True, slots=True)
class FileSearchRequest:
    workspace: WorkspaceSnapshot
    query: str
    limit: int


@dataclass(frozen=True, slots=True)
class FileSearchMatch:
    path: Path
    label: str
    section: int
    score: int


@dataclass(frozen=True, slots=True)
class ContentSearchRequest:
    workspace: WorkspaceSnapshot
    query: str
    max_matches: int = 2_000
    max_files: int = 500

    @property
    def cache_key(self) -> tuple[str, str, bool, bool, int, int]:
        query = self.workspace.query
        return (
            self.workspace.revision.tree,
            self.query,
            query.show_hidden,
            query.skip_gitignored,
            max(1, self.max_matches),
            max(1, self.max_files),
        )


@dataclass(frozen=True, slots=True)
class ContentSearchResult:
    matches: tuple[tuple[Path, tuple[ContentMatch, ...]], ...]
    truncated: bool
    error: str | None = None

    @classmethod
    def create(
        cls,
        matches: dict[Path, list[ContentMatch]],
        truncated: bool,
        error: str | None = None,
    ) -> "ContentSearchResult":
        return cls(
            tuple((path.resolve(), tuple(items)) for path, items in matches.items()),
            bool(truncated),
            error,
        )

    def as_dict(self) -> dict[Path, list[ContentMatch]]:
        return {path: list(items) for path, items in self.matches}


@dataclass(frozen=True, slots=True)
class ContentMatchesAdded:
    matches: tuple[ContentMatch, ...]


@dataclass(frozen=True, slots=True)
class ContentSearchFinished:
    result: ContentSearchResult


ContentSearchUpdate: TypeAlias = ContentMatchesAdded | ContentSearchFinished
