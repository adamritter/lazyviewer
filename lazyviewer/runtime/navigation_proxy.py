"""Late-bound proxy for navigation operations used during wiring."""

from __future__ import annotations

from pathlib import Path

from ..tree_pane.panels.picker import NavigationPickerOps


class NavigationProxy:
    """Late-bound proxy exposing navigation operations before ops construction."""

    def __init__(self) -> None:
        """Initialize proxy with no bound navigation operations."""
        self._ops: NavigationPickerOps | None = None

    def bind(self, ops: NavigationPickerOps) -> None:
        """Attach concrete navigation operations implementation."""
        self._ops = ops

    def current_jump_location(self):
        """Delegate current jump-location lookup to bound navigation ops."""
        assert self._ops is not None
        return self._ops.current_jump_location()

    def record_jump_if_changed(self, origin: object) -> None:
        """Delegate conditional jump-history recording to bound navigation ops."""
        assert self._ops is not None
        self._ops.record_jump_if_changed(origin)

    def jump_to_path(self, target: Path) -> None:
        """Delegate path jump request to bound navigation ops."""
        assert self._ops is not None
        self._ops.jump_to_path(target)

    def jump_to_line(self, line_number: int) -> None:
        """Delegate line jump request to bound navigation ops."""
        assert self._ops is not None
        self._ops.jump_to_line(line_number)


__all__ = ["NavigationProxy"]
