"""Map decoded terminal tokens to typed application input commands."""

from __future__ import annotations

from dataclasses import dataclass

from ..session import SessionState


@dataclass(frozen=True, slots=True)
class IgnoreInput:
    pass


@dataclass(frozen=True, slots=True)
class AdjustPaneWidth:
    delta: int


@dataclass(frozen=True, slots=True)
class DispatchKey:
    key: str


InputCommand = IgnoreInput | AdjustPaneWidth | DispatchKey


def map_input(raw_key: str, state: SessionState) -> InputCommand:
    """Normalize CR/LF and resize tokens before mode-specific dispatch."""
    interface = state.interface
    if interface.skip_next_lf and raw_key == "ENTER_LF":
        interface.skip_next_lf = False
        return IgnoreInput()

    if raw_key == "ENTER_CR":
        interface.skip_next_lf = True
        key = "ENTER"
    elif raw_key == "ENTER_LF":
        interface.skip_next_lf = False
        key = (
            "CTRL_J"
            if state.filter.active and state.filter.editing and not state.picker.active
            else "ENTER"
        )
    else:
        interface.skip_next_lf = False
        key = raw_key

    if key == "SHIFT_LEFT":
        return AdjustPaneWidth(-2)
    if key == "SHIFT_RIGHT":
        return AdjustPaneWidth(2)
    return DispatchKey(key)


__all__ = ["AdjustPaneWidth", "DispatchKey", "IgnoreInput", "InputCommand", "map_input"]
