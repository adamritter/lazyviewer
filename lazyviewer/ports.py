"""Small structural interfaces shared at component boundaries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .search.content import ContentMatch
from .tree_model import TreeEntry


class RebuildScreenLines(Protocol):
    def __call__(
        self,
        columns: int | None = None,
        preserve_scroll: bool = True,
    ) -> None: ...


class PreviewSelectedEntry(Protocol):
    def __call__(self, force: bool = False) -> None: ...


class RefreshPreview(Protocol):
    def __call__(
        self,
        *,
        reset_scroll: bool = True,
        reset_dir_budget: bool = False,
        force_rebuild: bool = False,
    ) -> None: ...


class RefreshGitStatus(Protocol):
    def __call__(self, force: bool = False) -> None: ...


class RebuildTreeEntries(Protocol):
    def __call__(
        self,
        preferred_path: Path | None = None,
        center_selection: bool = False,
        force_first_file: bool = False,
        preferred_workspace_root: Path | None = None,
        preferred_workspace_section: int | None = None,
        content_matches_override: dict[Path, list[ContentMatch]] | None = None,
        content_truncated_override: bool | None = None,
    ) -> None: ...


class ApplyTreeFilterQuery(Protocol):
    def __call__(
        self,
        query: str,
        preview_selection: bool = False,
        select_first_file: bool = False,
        debounce_prompt_row: bool = False,
    ) -> None: ...


class RequestDirectoryPreview(Protocol):
    def __call__(
        self,
        target: Path,
        *,
        reset_scroll: bool = True,
        reset_dir_budget: bool = False,
    ) -> None: ...


class SynchronizeTree(Protocol):
    def __call__(
        self,
        preferred_path: Path,
        force_rebuild: bool = False,
    ) -> None: ...


class BuildScreenLines(Protocol):
    def __call__(self, text: str, width: int, wrap: bool) -> list[str]: ...


class HelpPanelRowCount(Protocol):
    def __call__(
        self,
        max_lines: int,
        show_help: bool,
        *,
        browser_visible: bool = True,
        tree_filter_active: bool = False,
        tree_filter_mode: str = "files",
        tree_filter_editing: bool = False,
    ) -> int: ...


class BuildTreeEntries(Protocol):
    def __call__(
        self,
        root: Path,
        expanded: set[Path],
        show_hidden: bool,
        skip_gitignored: bool = False,
        git_status_overlay: dict[Path, int] | None = None,
        doc_summary_for_path: Callable[[Path, int | None], str | None] | None = None,
        include_doc_summaries: bool = False,
        workspace_root: Path | None = None,
        workspace_section: int | None = None,
    ) -> list[TreeEntry]: ...


__all__ = [
    "ApplyTreeFilterQuery",
    "BuildScreenLines",
    "BuildTreeEntries",
    "HelpPanelRowCount",
    "PreviewSelectedEntry",
    "RebuildScreenLines",
    "RebuildTreeEntries",
    "RefreshGitStatus",
    "RefreshPreview",
    "RequestDirectoryPreview",
    "SynchronizeTree",
]
