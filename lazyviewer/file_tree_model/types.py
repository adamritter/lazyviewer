"""Domain datatypes for filesystem-backed file tree entries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileEntry:
    """Domain file entry containing metadata observed from filesystem/git."""

    path: Path
    file_size: int | None = None
    mtime_ns: int | None = None
    git_status_flags: int = 0
    doc_summary: str | None = None


@dataclass(frozen=True)
class DirectoryEntry:
    """Domain directory entry with recursively nested children."""

    path: Path
    mtime_ns: int | None = None
    git_status_flags: int = 0
    children: tuple["FileTreeEntry", ...] = ()


FileTreeEntry = DirectoryEntry | FileEntry


__all__ = [
    "FileEntry",
    "DirectoryEntry",
    "FileTreeEntry",
]
