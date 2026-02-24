"""Shared symbol datatypes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolEntry:
    """Normalized symbol record used by picker and sticky-header features."""

    kind: str
    name: str
    line: int
    column: int
    label: str
