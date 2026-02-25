"""Integration-heavy tests for ``lazyviewer.runtime.app`` wiring.

Covers git/watch refresh behavior, key-driven state transitions, and search flows.
These tests ensure runtime callbacks and state orchestration stay coherent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from lazyviewer.runtime import app as app_runtime
from lazyviewer.render.ansi import ANSI_ESCAPE_RE
from lazyviewer.runtime.screen import (
    _centered_scroll_start,
    _first_git_change_screen_line,
    _tree_order_key_for_relative_path,
)
from lazyviewer.git_status import GIT_STATUS_CHANGED
from lazyviewer.runtime.navigation import JumpLocation
from lazyviewer.render import help_panel_row_count, render_dual_page
from lazyviewer.search.content import ContentMatch


def _callback(kwargs: dict[str, object], name: str):
    callbacks = kwargs["callbacks"]
    if hasattr(callbacks, name):
        return getattr(callbacks, name)

    tree_pane = getattr(callbacks, "tree_pane", None)
    source_pane = getattr(callbacks, "source_pane", None)
    layout = getattr(callbacks, "layout", None)
    if tree_pane is None or source_pane is None or layout is None:
        raise AttributeError(f"{type(callbacks).__name__} has no callback {name!r}")

    mapping = {
        "activate_tree_filter_selection": tree_pane.filter.activate_tree_filter_selection,
        "apply_tree_filter_query": tree_pane.filter.apply_tree_filter_query,
        "close_tree_filter": tree_pane.filter.close_tree_filter,
        "handle_normal_key": callbacks.handle_normal_key,
        "handle_tree_mouse_click": tree_pane.handle_tree_mouse_click,
        "handle_tree_mouse_wheel": source_pane.handle_tree_mouse_wheel,
        "maybe_refresh_git_watch": callbacks.maybe_refresh_git_watch,
        "open_tree_filter": tree_pane.filter.open_tree_filter,
        "rebuild_screen_lines": layout.rebuild_screen_lines,
        "refresh_git_status_overlay": callbacks.refresh_git_status_overlay,
        "save_left_pane_width": callbacks.save_left_pane_width,
        "set_named_mark": tree_pane.navigation.set_named_mark,
        "tick_source_selection_drag": getattr(callbacks, "tick_source_selection_drag", lambda: None),
    }
    if name not in mapping:
        raise AttributeError(f"{type(callbacks).__name__} has no callback {name!r}")
    return mapping[name]


class AppRuntimeCoreTests(unittest.TestCase):
    def test_first_git_change_screen_line_handles_plain_and_diff_markers(self) -> None:
        plain_lines = [
            "  unchanged",
            "- removed",
            "+ added",
        ]
        self.assertEqual(_first_git_change_screen_line(plain_lines), 1)

        ansi_lines = [
            "\033[2;38;5;245m  \033[0munchanged",
            "\033[38;5;42m+ \033[0madded",
        ]
        self.assertEqual(_first_git_change_screen_line(ansi_lines), 1)

        non_diff_background_lines = [
            "\033[38;5;252munchanged\033[0m",
            "\033[38;5;252;48;5;22madded\033[0m",
        ]
        self.assertIsNone(_first_git_change_screen_line(non_diff_background_lines))

        truecolor_background_lines = [
            "\033[38;5;252munchanged\033[0m",
            "\033[38;2;220;220;220;48;2;36;74;52madded\033[0m",
        ]
        self.assertEqual(_first_git_change_screen_line(truecolor_background_lines), 1)

    def test_first_git_change_screen_line_returns_none_without_markers(self) -> None:
        self.assertIsNone(_first_git_change_screen_line(["x = 1", "y = 2"]))

    def test_centered_scroll_start_clamps_and_interpolates(self) -> None:
        self.assertEqual(_centered_scroll_start(target_line=30, max_start=40, visible_rows=12), 26)
        self.assertEqual(_centered_scroll_start(target_line=1, max_start=40, visible_rows=12), 0)
        self.assertEqual(_centered_scroll_start(target_line=120, max_start=40, visible_rows=12), 36)

    def test_tree_order_key_matches_dirs_first_tree_sort(self) -> None:
        relative_paths = [
            Path("aaa.py"),
            Path("zzz/inner.py"),
            Path("bbb.py"),
            Path("bbb/aaa.py"),
            Path("zzz.py"),
        ]
        ordered = sorted(relative_paths, key=_tree_order_key_for_relative_path)
        self.assertEqual(
            ordered,
            [
                Path("bbb/aaa.py"),
                Path("zzz/inner.py"),
                Path("aaa.py"),
                Path("bbb.py"),
                Path("zzz.py"),
            ],
        )
        self.assertLess(
            _tree_order_key_for_relative_path(Path("zzz"), is_dir=True),
            _tree_order_key_for_relative_path(Path("zzz/inner.py")),
        )
