"""Picker-mode keyboard handling compatibility wrapper."""

from __future__ import annotations

from collections.abc import Callable

from ..runtime.state import AppState
from ..tree_pane.panels.picker import key_dispatch


class _PickerDispatchAdapter:
    """Adapter exposing NavigationController-like API over callback functions."""

    def __init__(
        self,
        *,
        state: AppState,
        close_picker: Callable[[], None],
        refresh_command_picker_matches: Callable[..., None],
        activate_picker_selection: Callable[[], bool],
        visible_content_rows: Callable[[], int],
        refresh_active_picker_matches: Callable[..., None],
    ) -> None:
        self.state = state
        self._close_picker = close_picker
        self._refresh_command_picker_matches = refresh_command_picker_matches
        self._activate_picker_selection = activate_picker_selection
        self._visible_content_rows = visible_content_rows
        self._refresh_active_picker_matches = refresh_active_picker_matches

    def close_picker(self) -> None:
        self._close_picker()

    def refresh_command_picker_matches(self, reset_selection: bool = False) -> None:
        self._refresh_command_picker_matches(reset_selection=reset_selection)

    def activate_picker_selection(self) -> bool:
        return self._activate_picker_selection()

    def visible_content_rows(self) -> int:
        return self._visible_content_rows()

    def refresh_active_picker_matches(self, reset_selection: bool = False) -> None:
        self._refresh_active_picker_matches(reset_selection=reset_selection)


def handle_picker_key(
    key: str,
    state: AppState,
    double_click_seconds: float,
    *,
    close_picker: Callable[[], None],
    refresh_command_picker_matches: Callable[..., None],
    activate_picker_selection: Callable[[], bool],
    visible_content_rows: Callable[[], int],
    refresh_active_picker_matches: Callable[..., None],
) -> tuple[bool, bool]:
    """Handle one key while picker is active.

    Returns ``(handled, should_quit)`` so the main loop can stop event
    propagation and optionally terminate the application.
    """
    adapter = _PickerDispatchAdapter(
        state=state,
        close_picker=close_picker,
        refresh_command_picker_matches=refresh_command_picker_matches,
        activate_picker_selection=activate_picker_selection,
        visible_content_rows=visible_content_rows,
        refresh_active_picker_matches=refresh_active_picker_matches,
    )
    return key_dispatch.handle_picker_key(adapter, key, double_click_seconds)
