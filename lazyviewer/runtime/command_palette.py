"""Static command palette item definitions."""

from __future__ import annotations

COMMAND_PALETTE_ITEMS: tuple[tuple[str, str], ...] = (
    ("filter_files", "Filter files (Ctrl+P)"),
    ("search_content", "Search content (/)"),
    ("open_symbols", "Open symbol outline (s)"),
    ("history_back", "Jump back (Alt+Left)"),
    ("history_forward", "Jump forward (Alt+Right)"),
    ("toggle_tree", "Toggle tree pane (t)"),
    ("toggle_wrap", "Toggle wrap (w)"),
    ("toggle_hidden", "Toggle hidden files (.)"),
    ("toggle_help", "Toggle help (?)"),
    ("reroot_selected", "Set root to selected (r)"),
    ("reroot_parent", "Set root to parent (R)"),
    ("quit", "Quit (q)"),
)

__all__ = ["COMMAND_PALETTE_ITEMS"]
