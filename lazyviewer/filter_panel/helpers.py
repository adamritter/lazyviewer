"""Small shared helpers for tree-filter behavior."""

from __future__ import annotations


def skip_gitignored_for_hidden_mode(show_hidden: bool) -> bool:
    """Return whether gitignored paths should be excluded for current hidden mode."""
    # Hidden mode should reveal both dotfiles and gitignored paths.
    return not show_hidden
