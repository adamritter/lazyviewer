"""Runtime interpreter for typed session effects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..preview import PreviewDocument, PreviewRequest
from ..session import (
    ApplyPreview,
    RefreshPreview,
    ReflowPreview,
    SavePaneWidth,
    SessionAction,
    SessionEffect,
    SessionState,
    SynchronizeWorkspace,
    update,
)


@dataclass(frozen=True, slots=True)
class SessionEffectHandlers:
    reflow_preview: Callable[[int], None]
    save_pane_width: Callable[[int, int], None]
    synchronize_workspace: Callable[[Path], None]
    refresh_preview: Callable[[bool, bool], None]
    apply_preview: Callable[[PreviewRequest, PreviewDocument, bool], None]


class SessionCoordinator:
    """Single root that updates session state and interprets declared effects."""

    def __init__(
        self,
        state: SessionState,
        handlers: SessionEffectHandlers,
    ) -> None:
        self.state = state
        self.handlers = handlers

    def dispatch(self, action: SessionAction) -> tuple[SessionEffect, ...]:
        effects = update(self.state, action)
        for effect in effects:
            if isinstance(effect, ReflowPreview):
                self.handlers.reflow_preview(effect.columns)
            elif isinstance(effect, SavePaneWidth):
                self.handlers.save_pane_width(effect.columns, effect.left_width)
            elif isinstance(effect, SynchronizeWorkspace):
                self.handlers.synchronize_workspace(effect.preferred_path)
            elif isinstance(effect, RefreshPreview):
                self.handlers.refresh_preview(
                    effect.reset_scroll,
                    effect.reset_directory_budget,
                )
            elif isinstance(effect, ApplyPreview):
                self.handlers.apply_preview(
                    effect.request,
                    effect.document,
                    effect.reset_scroll,
                )
            else:  # pragma: no cover - effect union is exhaustive.
                raise TypeError(f"unsupported session effect: {type(effect)!r}")
        return effects


__all__ = ["SessionCoordinator", "SessionEffectHandlers"]
