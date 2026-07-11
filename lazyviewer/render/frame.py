"""Immutable logical-screen values shared by renderers and terminal drivers.

``Frame.text`` remains the canonical full repaint used by capture tests and as
the recovery path after resize or terminal suspension.  ``Frame.surfaces``
adds a retained representation: independently positioned rectangular panes
whose rows can be compared without parsing the full ANSI stream.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rect:
    """Zero-based terminal-cell rectangle."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ValueError("surface coordinates must be non-negative")
        if self.width < 1 or self.height < 1:
            raise ValueError("surface dimensions must be positive")


@dataclass(frozen=True, slots=True)
class Surface:
    """Named rectangular group of independently repaintable ANSI rows."""

    name: str
    rect: Rect
    rows: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("surface name must not be empty")
        if len(self.rows) != self.rect.height:
            raise ValueError("surface row count must equal rectangle height")


@dataclass(frozen=True, slots=True)
class Frame:
    """One complete screen plus optional independently diffable surfaces."""

    text: str
    width: int = 0
    height: int = 0
    surfaces: tuple[Surface, ...] = ()

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            raise ValueError("frame dimensions must be non-negative")
        names = tuple(surface.name for surface in self.surfaces)
        if len(names) != len(set(names)):
            raise ValueError("surface names must be unique within a frame")
        for surface in self.surfaces:
            rect = surface.rect
            if self.width and rect.x + rect.width > self.width:
                raise ValueError("surface extends beyond frame width")
            if self.height and rect.y + rect.height > self.height:
                raise ValueError("surface extends beyond frame height")

    @property
    def data(self) -> bytes:
        return self.text.encode("utf-8", errors="replace")

    @property
    def is_structured(self) -> bool:
        """Return whether this frame supports incremental presentation."""
        return bool(self.width and self.height and self.surfaces)


__all__ = ["Frame", "Rect", "Surface"]
