"""Source-pane preview orchestration over typed loading and presentation APIs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..preview import (
    DIRECTORY_GROWTH_STEP,
    DIRECTORY_HARD_MAX_ENTRIES,
    DIRECTORY_INITIAL_MAX_ENTRIES,
    DiffDocument,
    DirectoryDocument,
    PreviewDocument,
    PreviewRequest,
    PreviewService,
    RenderedPreview,
)
from ..ports import RebuildScreenLines
from ..runtime.screen import _centered_scroll_start, _first_git_change_screen_line
from ..session import SessionState
from .presenter import PreviewPresenter


class PreviewController:
    """Own source-preview request, apply, and directory-budget behavior."""

    def __init__(
        self,
        *,
        state: SessionState,
        service: PreviewService,
        presenter: PreviewPresenter,
        rebuild_screen_lines: RebuildScreenLines,
        visible_content_rows: Callable[[], int],
    ) -> None:
        self.state = state
        self.service = service
        self.presenter = presenter
        self._rebuild_screen_lines = rebuild_screen_lines
        self._visible_content_rows = visible_content_rows

    @property
    def workspace_revision(self) -> str:
        snapshot = self.state.workspace.snapshot
        return snapshot.revision.value if snapshot is not None else "unversioned"

    def request(self, target: Path, *, max_entries: int | None = None) -> PreviewRequest:
        prefer_git_diff = self.state.git.enabled and not (
            self.state.filter.active
            and self.state.filter.mode == "content"
            and bool(self.state.filter.query)
        )
        include_git_status = self.state.git.enabled and (
            prefer_git_diff or target.is_dir()
        )
        return PreviewRequest.create(
            target,
            workspace_revision=self.workspace_revision,
            show_hidden=self.state.workspace.show_hidden,
            skip_gitignored=not self.state.workspace.show_hidden,
            prefer_git_diff=prefer_git_diff,
            directory_max_entries=(
                self.state.preview.directory_max_entries if max_entries is None else max_entries
            ),
            git_status=(self.state.git.status if include_git_status else None),
            show_size_labels=self.state.workspace.show_sizes,
        )

    def load(self, request: PreviewRequest) -> tuple[PreviewDocument, RenderedPreview]:
        document = self.service.load(request)
        return document, self.presenter.present(document)

    def refresh(
        self,
        *,
        reset_scroll: bool = True,
        reset_dir_budget: bool = False,
        force_rebuild: bool = False,
    ) -> None:
        if force_rebuild:
            self.service.clear()
        target = self.state.workspace.current_path.resolve()
        if target.is_dir():
            if reset_dir_budget or self.state.preview.directory_path != target:
                self.state.preview.directory_max_entries = self.initial_max_entries(
                    self._visible_content_rows()
                )
            max_entries = self.state.preview.directory_max_entries
        else:
            max_entries = DIRECTORY_INITIAL_MAX_ENTRIES
        request = self.request(target, max_entries=max_entries)
        document, rendered = self.load(request)
        self.apply(request, document, rendered, reset_scroll=reset_scroll)

    def apply(
        self,
        request: PreviewRequest,
        document: PreviewDocument,
        rendered: RenderedPreview,
        *,
        reset_scroll: bool,
    ) -> bool:
        """Apply a result if it still belongs to the selected target and revision."""
        if request.target != self.state.workspace.current_path.resolve():
            return False
        if request.workspace_revision != self.workspace_revision:
            return False
        self.state.preview.document = document
        self.state.preview.rendered = rendered.text
        self._rebuild_screen_lines(preserve_scroll=not reset_scroll)
        if reset_scroll and isinstance(document, DiffDocument):
            first_change = _first_git_change_screen_line(self.state.preview.lines)
            if first_change is not None:
                self.state.preview.scroll = _centered_scroll_start(
                    first_change,
                    self.state.preview.max_scroll,
                    self._visible_content_rows(),
                )
        is_directory = isinstance(document, DirectoryDocument)
        self.state.preview.directory_truncated = document.truncated if is_directory else False
        self.state.preview.directory_path = document.path if is_directory else None
        self.state.preview.image_path = rendered.image_path
        self.state.preview.image_format = rendered.image_format
        self.state.preview.is_git_diff = isinstance(document, DiffDocument)
        if reset_scroll:
            self.state.preview.horizontal_scroll = 0
        return True

    @staticmethod
    def initial_max_entries(visible_rows: int) -> int:
        return min(DIRECTORY_INITIAL_MAX_ENTRIES, max(1, visible_rows - 4))

    @staticmethod
    def growth_step(visible_rows: int) -> int:
        return min(DIRECTORY_GROWTH_STEP, max(16, max(1, visible_rows) * 2))

    def prefetch_target_entries(
        self,
        *,
        min_headroom_screens: int = 4,
        target_headroom_screens: int = 12,
    ) -> int | None:
        state = self.state
        if state.preview.directory_path is None or not state.preview.directory_truncated:
            return None
        if state.workspace.current_path.resolve() != state.preview.directory_path:
            return None
        if state.preview.directory_max_entries >= DIRECTORY_HARD_MAX_ENTRIES:
            return None
        visible_rows = max(1, self._visible_content_rows())
        minimum = visible_rows * max(1, min_headroom_screens)
        if max(0, state.preview.max_scroll - state.preview.scroll) >= minimum:
            return None
        target = state.preview.scroll + visible_rows * max(1, target_headroom_screens)
        next_max = min(
            DIRECTORY_HARD_MAX_ENTRIES,
            max(state.preview.directory_max_entries + self.growth_step(visible_rows), target),
        )
        return next_max if next_max > state.preview.directory_max_entries else None

    def maybe_grow(self) -> bool:
        state = self.state
        if state.preview.directory_path is None or not state.preview.directory_truncated:
            return False
        if state.workspace.current_path.resolve() != state.preview.directory_path:
            return False
        if state.preview.directory_max_entries >= DIRECTORY_HARD_MAX_ENTRIES:
            return False
        visible_rows = max(1, self._visible_content_rows())
        if state.preview.scroll < max(0, state.preview.max_scroll - max(1, visible_rows // 3)):
            return False
        previous_line_count = len(state.preview.lines)
        state.preview.directory_max_entries = min(
            DIRECTORY_HARD_MAX_ENTRIES,
            max(
                state.preview.directory_max_entries + self.growth_step(visible_rows),
                state.preview.scroll + visible_rows * 3,
            ),
        )
        self.refresh(reset_scroll=False, reset_dir_budget=False)
        return len(state.preview.lines) > previous_line_count

    def toggle_size_labels(self) -> None:
        self.state.workspace.show_sizes = not self.state.workspace.show_sizes
        if self.state.workspace.current_path.resolve().is_dir():
            self.refresh(reset_scroll=False, reset_dir_budget=False)
        self.state.interface.dirty = True


__all__ = ["PreviewController"]
