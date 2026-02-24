"""Compatibility wrappers for tree/git watch refresh helpers.

Watch refresh orchestration now lives under ``lazyviewer.tree_pane.watch``.
"""

from ..tree_pane.watch import WatchRefreshContext, refresh_git_status_overlay as _refresh_git_status_overlay

__all__ = ["WatchRefreshContext", "_refresh_git_status_overlay"]

