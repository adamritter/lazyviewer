from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .tree import TreeEntry


@dataclass
class AppState:
    current_path: Path
    tree_root: Path
    expanded: set[Path]
    show_hidden: bool
    tree_entries: list[TreeEntry]
    selected_idx: int
    rendered: str
    lines: list[str]
    start: int
    tree_start: int
    text_x: int
    left_width: int
    right_width: int
    usable: int
    max_start: int
    last_right_width: int
    browser_visible: bool = True
    show_help: bool = False
    dirty: bool = True
    skip_next_lf: bool = False
    count_buffer: str = ""
    last_click_idx: int = -1
    last_click_time: float = 0.0
