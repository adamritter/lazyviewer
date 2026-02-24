"""Tree entry datatypes used across tree-pane modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TreeEntry:
    """One rendered row in the tree pane (path row or synthetic search-hit row)."""

    path: Path
    depth: int
    is_dir: bool
    file_size: int | None = None
    kind: str = "path"
    display: str | None = None
    line: int | None = None
    column: int | None = None
