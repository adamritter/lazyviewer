"""Semantic preview requests and document variants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    target: Path
    workspace_revision: str
    show_hidden: bool
    skip_gitignored: bool
    prefer_git_diff: bool = True
    directory_max_depth: int = 32
    directory_max_entries: int = 1_000
    git_status: tuple[tuple[Path, int], ...] = ()
    show_size_labels: bool = True

    @classmethod
    def create(
        cls,
        target: Path,
        *,
        workspace_revision: str,
        show_hidden: bool,
        skip_gitignored: bool,
        prefer_git_diff: bool = True,
        directory_max_depth: int = 32,
        directory_max_entries: int = 1_000,
        git_status: dict[Path, int] | None = None,
        show_size_labels: bool = True,
    ) -> "PreviewRequest":
        return cls(
            target=target.resolve(),
            workspace_revision=workspace_revision,
            show_hidden=show_hidden,
            skip_gitignored=skip_gitignored,
            prefer_git_diff=prefer_git_diff,
            directory_max_depth=directory_max_depth,
            directory_max_entries=directory_max_entries,
            git_status=tuple(sorted((git_status or {}).items(), key=lambda item: str(item[0]))),
            show_size_labels=show_size_labels,
        )


@dataclass(frozen=True, slots=True)
class TextDocument:
    path: Path
    text: str
    file_size: int


@dataclass(frozen=True, slots=True)
class BinaryDocument:
    path: Path
    file_size: int


@dataclass(frozen=True, slots=True)
class ImageDocument:
    path: Path
    format: str


@dataclass(frozen=True, slots=True)
class ErrorDocument:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class DirectoryRow:
    path: Path
    depth: int
    is_dir: bool
    is_last: bool
    ancestor_last: tuple[bool, ...]
    file_size: int | None = None
    git_status_flags: int = 0
    doc_summary: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DirectoryDocument:
    path: Path
    rows: tuple[DirectoryRow, ...]
    truncated: bool
    max_entries: int
    git_status_flags: int = 0


@dataclass(frozen=True, slots=True)
class DiffLine:
    kind: str  # context | added | removed
    text: str
    source_line: int | None


@dataclass(frozen=True, slots=True)
class DiffDocument:
    path: Path
    lines: tuple[DiffLine, ...]


PreviewDocument: TypeAlias = (
    TextDocument
    | BinaryDocument
    | ImageDocument
    | ErrorDocument
    | DirectoryDocument
    | DiffDocument
)


@dataclass(frozen=True, slots=True)
class RenderedPreview:
    """Presentation result retaining the semantic source document."""

    document: PreviewDocument
    text: str

    @property
    def is_directory(self) -> bool:
        return isinstance(self.document, DirectoryDocument)

    @property
    def truncated(self) -> bool:
        return self.document.truncated if isinstance(self.document, DirectoryDocument) else False

    @property
    def image_path(self) -> Path | None:
        return self.document.path if isinstance(self.document, ImageDocument) else None

    @property
    def image_format(self) -> str | None:
        return self.document.format if isinstance(self.document, ImageDocument) else None

    @property
    def is_git_diff_preview(self) -> bool:
        return isinstance(self.document, DiffDocument)
