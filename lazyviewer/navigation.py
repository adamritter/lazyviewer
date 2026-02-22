"""Navigation primitives: jump locations, history, and mark-key validation.

This module intentionally has no UI concerns.
It provides normalized location objects used across runtime handlers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

MAX_JUMP_HISTORY = 256


@dataclass(frozen=True)
class JumpLocation:
    path: Path
    start: int = 0
    text_x: int = 0

    def normalized(self) -> JumpLocation:
        try:
            resolved = self.path.resolve()
        except Exception:
            resolved = self.path
        return JumpLocation(
            path=resolved,
            start=max(0, self.start),
            text_x=max(0, self.text_x),
        )


class JumpHistory:
    def __init__(self, max_entries: int = MAX_JUMP_HISTORY) -> None:
        self.max_entries = max(1, max_entries)
        self.back: list[JumpLocation] = []
        self.forward: list[JumpLocation] = []

    def _append_unique(self, stack: list[JumpLocation], location: JumpLocation) -> None:
        location = location.normalized()
        if stack and stack[-1] == location:
            return
        stack.append(location)
        overflow = len(stack) - self.max_entries
        if overflow > 0:
            del stack[:overflow]

    def record(self, origin: JumpLocation) -> None:
        self._append_unique(self.back, origin)
        self.forward.clear()

    def go_back(self, current: JumpLocation) -> JumpLocation | None:
        current = current.normalized()
        while self.back and self.back[-1] == current:
            self.back.pop()
        if not self.back:
            return None
        target = self.back.pop()
        self._append_unique(self.forward, current)
        return target

    def go_forward(self, current: JumpLocation) -> JumpLocation | None:
        current = current.normalized()
        while self.forward and self.forward[-1] == current:
            self.forward.pop()
        if not self.forward:
            return None
        target = self.forward.pop()
        self._append_unique(self.back, current)
        return target


def is_named_mark_key(key: str) -> bool:
    return len(key) == 1 and key.isprintable() and not key.isspace()
