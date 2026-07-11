"""Feature-owned mutable state for one interactive lazyviewer session."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..preview import PreviewDocument
from .navigation import JumpHistory, JumpLocation
from ..tree_model import TreeEntry
from ..ui_theme import DEFAULT_THEME, UITheme
from ..workspace import WorkspaceSnapshot


@dataclass(slots=True)
class WorkspaceViewState:
    current_path: Path
    active_root: Path
    expanded: set[Path]
    show_hidden: bool
    entries: list[TreeEntry]
    selected: int
    roots: list[Path] = field(default_factory=list)
    expanded_by_root: list[set[Path]] = field(default_factory=list)
    render_expanded: set[Path] = field(default_factory=set)
    show_sizes: bool = True
    scroll: int = 0
    snapshot: WorkspaceSnapshot | None = None


@dataclass(slots=True)
class PreviewViewState:
    rendered: str
    lines: list[str]
    scroll: int = 0
    horizontal_scroll: int = 0
    wrap: bool = False
    max_scroll: int = 0
    directory_max_entries: int = 400
    directory_truncated: bool = False
    directory_path: Path | None = None
    image_path: Path | None = None
    image_format: str | None = None
    is_git_diff: bool = False
    document: PreviewDocument | None = None
    selection_anchor: tuple[int, int] | None = None
    selection_focus: tuple[int, int] | None = None


@dataclass(slots=True)
class LayoutState:
    left_width: int
    right_width: int
    usable_rows: int
    last_right_width: int
    browser_visible: bool = True
    show_help: bool = False
    theme_name: str = DEFAULT_THEME.name
    theme: UITheme = field(default_factory=lambda: DEFAULT_THEME)


@dataclass(slots=True)
class FilterState:
    active: bool = False
    editing: bool = False
    mode: str = "files"
    query: str = ""
    match_count: int = 0
    truncated: bool = False
    loading: bool = False
    prompt_row_visible: bool = True
    collapsed_dirs: set[Path] = field(default_factory=set)
    previous_browser_visible: bool | None = None
    origin: JumpLocation | None = None


@dataclass(slots=True)
class PickerState:
    active: bool = False
    mode: str = "symbols"
    focus: str = "query"
    message: str = ""
    query: str = ""
    selected: int = 0
    list_start: int = 0
    matches: list[Path] = field(default_factory=list)
    match_labels: list[str] = field(default_factory=list)
    match_lines: list[int] = field(default_factory=list)
    match_commands: list[str] = field(default_factory=list)
    symbol_file: Path | None = None
    symbol_labels: list[str] = field(default_factory=list)
    symbol_lines: list[int] = field(default_factory=list)
    command_ids: list[str] = field(default_factory=list)
    command_labels: list[str] = field(default_factory=list)
    previous_browser_visible: bool | None = None


@dataclass(slots=True)
class GitState:
    status: dict[Path, int] = field(default_factory=dict)
    last_refresh: float = 0.0
    enabled: bool = True


@dataclass(slots=True)
class NavigationState:
    history: JumpHistory = field(default_factory=JumpHistory)
    marks: dict[str, JumpLocation] = field(default_factory=dict)
    pending_mark_set: bool = False
    pending_mark_jump: bool = False


@dataclass(slots=True)
class InterfaceState:
    dirty: bool = True
    status_message: str = ""
    status_message_until: float = 0.0
    skip_next_lf: bool = False
    count_buffer: str = ""
    last_click_index: int = -1
    last_click_time: float = 0.0


@dataclass(slots=True)
class SessionState:
    """Root session composed from independently owned feature state."""

    workspace: WorkspaceViewState
    preview: PreviewViewState
    layout: LayoutState
    filter: FilterState = field(default_factory=FilterState)
    picker: PickerState = field(default_factory=PickerState)
    git: GitState = field(default_factory=GitState)
    navigation: NavigationState = field(default_factory=NavigationState)
    interface: InterfaceState = field(default_factory=InterfaceState)


__all__ = [
    "FilterState",
    "GitState",
    "InterfaceState",
    "LayoutState",
    "NavigationState",
    "PickerState",
    "PreviewViewState",
    "SessionState",
    "WorkspaceViewState",
]
