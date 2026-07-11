"""Immutable workspace contracts shared by tree, search, and runtime layers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceQuery:
    """Normalized inputs that define one visible multi-root workspace."""

    roots: tuple[Path, ...]
    expanded_by_root: tuple[frozenset[Path], ...]
    show_hidden: bool
    skip_gitignored: bool

    @classmethod
    def create(
        cls,
        roots: list[Path] | tuple[Path, ...],
        expanded_by_root: list[set[Path]] | tuple[frozenset[Path], ...],
        *,
        show_hidden: bool,
        skip_gitignored: bool,
    ) -> "WorkspaceQuery":
        """Resolve roots and constrain each expansion set to its root."""
        normalized_roots = tuple(root.resolve() for root in roots)
        normalized_expanded: list[frozenset[Path]] = []
        for section, root in enumerate(normalized_roots):
            source = expanded_by_root[section] if section < len(expanded_by_root) else {root}
            scoped: set[Path] = {root}
            for candidate in source:
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                if resolved.is_relative_to(root):
                    scoped.add(resolved)
            normalized_expanded.append(frozenset(scoped))
        return cls(
            roots=normalized_roots,
            expanded_by_root=tuple(normalized_expanded),
            show_hidden=bool(show_hidden),
            skip_gitignored=bool(skip_gitignored),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceRevision:
    """Opaque identities for tree/catalog and Git-visible workspace state."""

    tree: str
    git: str

    @property
    def value(self) -> str:
        return f"{self.tree}:{self.git}"


@dataclass(frozen=True, slots=True)
class WorkspaceRootSnapshot:
    """Observed filesystem and Git state for one workspace-root section."""

    path: Path
    expanded: frozenset[Path]
    tree_signature: str
    git_repo_root: Path | None
    git_dir: Path | None
    git_signature: str
    git_status: tuple[tuple[Path, int], ...]


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """Immutable multi-root observation consumed by application features."""

    query: WorkspaceQuery
    roots: tuple[WorkspaceRootSnapshot, ...]
    revision: WorkspaceRevision

    def merged_git_status(self) -> dict[Path, int]:
        """Merge per-root overlays while preserving flags from overlapping roots."""
        merged: dict[Path, int] = {}
        for root in self.roots:
            for path, flags in root.git_status:
                merged[path] = merged.get(path, 0) | int(flags)
        return merged


@dataclass(frozen=True, slots=True)
class WorkspaceDelta:
    """A refreshed snapshot plus the sections whose observations changed."""

    snapshot: WorkspaceSnapshot
    tree_changed_sections: tuple[int, ...]
    git_changed_sections: tuple[int, ...]

    @property
    def tree_changed(self) -> bool:
        return bool(self.tree_changed_sections)

    @property
    def git_changed(self) -> bool:
        return bool(self.git_changed_sections)


@dataclass(frozen=True, slots=True)
class WorkspaceFileIndex:
    """Project-file catalog aligned to workspace section order."""

    revision: WorkspaceRevision
    labels: tuple[str, ...]
    paths: tuple[Path, ...]
    sections: tuple[int, ...]

    def __post_init__(self) -> None:
        if not (len(self.labels) == len(self.paths) == len(self.sections)):
            raise ValueError("workspace file-index arrays must have equal lengths")

