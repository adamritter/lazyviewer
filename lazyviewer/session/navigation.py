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
    """Serializable viewport location for one path."""

    path: Path
    start: int = 0
    text_x: int = 0

    def normalized(self) -> JumpLocation:
        """Return a resolved, non-negative variant safe for persistence/history."""
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
    """Bounded back/forward stacks for location jumps.

    Adjacent duplicate locations are suppressed to avoid no-op navigation steps.
    """

    def __init__(self, max_entries: int = MAX_JUMP_HISTORY) -> None:
        """Create a jump history with bounded stack size."""
        self.max_entries = max(1, max_entries)
        self.back: list[JumpLocation] = []
        self.forward: list[JumpLocation] = []

    def _append_unique(self, stack: list[JumpLocation], location: JumpLocation) -> None:
        """Append a normalized location unless it duplicates the stack tail."""
        location = location.normalized()
        if stack and stack[-1] == location:
            return
        stack.append(location)
        overflow = len(stack) - self.max_entries
        if overflow > 0:
            del stack[:overflow]

    def record(self, origin: JumpLocation) -> None:
        """Push a new origin onto back stack and clear forward history."""
        self._append_unique(self.back, origin)
        self.forward.clear()

    def go_back(self, current: JumpLocation) -> JumpLocation | None:
        """Pop next back target and push current location onto forward stack."""
        current = current.normalized()
        while self.back and self.back[-1] == current:
            self.back.pop()
        if not self.back:
            return None
        target = self.back.pop()
        self._append_unique(self.forward, current)
        return target

    def go_forward(self, current: JumpLocation) -> JumpLocation | None:
        """Pop next forward target and push current location onto back stack."""
        current = current.normalized()
        while self.forward and self.forward[-1] == current:
            self.forward.pop()
        if not self.forward:
            return None
        target = self.forward.pop()
        self._append_unique(self.back, current)
        return target


def is_named_mark_key(key: str) -> bool:
    """Return whether key is a valid single-character named-mark identifier."""
    return len(key) == 1 and key.isprintable() and not key.isspace()
