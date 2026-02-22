from __future__ import annotations

from dataclasses import dataclass, field
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
    wrap_text: bool
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
    tree_filter_active: bool = False
    tree_filter_editing: bool = False
    tree_filter_mode: str = "files"
    tree_filter_query: str = ""
    tree_filter_match_count: int = 0
    tree_filter_truncated: bool = False
    tree_filter_prev_browser_visible: bool | None = None
    tree_render_expanded: set[Path] = field(default_factory=set)
    picker_active: bool = False
    picker_mode: str = "symbols"
    picker_focus: str = "query"
    picker_message: str = ""
    picker_query: str = ""
    picker_selected: int = 0
    picker_list_start: int = 0
    picker_matches: list[Path] = field(default_factory=list)
    picker_match_labels: list[str] = field(default_factory=list)
    picker_match_lines: list[int] = field(default_factory=list)
    picker_file_labels: list[str] = field(default_factory=list)
    picker_file_labels_folded: list[str] = field(default_factory=list)
    picker_files_root: Path | None = None
    picker_files_show_hidden: bool | None = None
    picker_symbol_file: Path | None = None
    picker_symbol_labels: list[str] = field(default_factory=list)
    picker_symbol_lines: list[int] = field(default_factory=list)
    picker_prev_browser_visible: bool | None = None
    dir_preview_max_entries: int = 400
    dir_preview_truncated: bool = False
    dir_preview_path: Path | None = None
    git_status_overlay: dict[Path, int] = field(default_factory=dict)
    git_status_last_refresh: float = 0.0
