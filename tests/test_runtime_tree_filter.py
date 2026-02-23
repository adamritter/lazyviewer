"""Tests for tree-filter runtime behavior.

Currently targets cached content-search reuse while editing queries.
Protects the no-recompute-on-backspace optimization path.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.navigation import JumpLocation
from lazyviewer.runtime_tree_filter import TreeFilterDeps, TreeFilterOps
from lazyviewer.state import AppState
from lazyviewer.tree import TreeEntry


def _make_state(root: Path) -> AppState:
    resolved_root = root.resolve()
    return AppState(
        current_path=resolved_root,
        tree_root=resolved_root,
        expanded={resolved_root},
        show_hidden=False,
        tree_entries=[TreeEntry(path=resolved_root, depth=0, is_dir=True)],
        selected_idx=0,
        rendered="",
        lines=[""],
        start=0,
        tree_start=0,
        text_x=0,
        wrap_text=False,
        left_width=24,
        right_width=80,
        usable=24,
        max_start=0,
        last_right_width=80,
    )


class RuntimeTreeFilterTests(unittest.TestCase):
    def test_content_search_reuses_cached_results_when_backspacing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "demo.py").write_text("alpha beta gamma\n", encoding="utf-8")
            state = _make_state(root)
            state.tree_filter_active = True
            state.tree_filter_mode = "content"

            ops = TreeFilterOps(
                TreeFilterDeps(
                    state=state,
                    visible_content_rows=lambda: 20,
                    rebuild_screen_lines=lambda **_kwargs: None,
                    preview_selected_entry=lambda **_kwargs: None,
                    current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
                    record_jump_if_changed=lambda _origin: None,
                    jump_to_path=lambda _target: None,
                    jump_to_line=lambda _line: None,
                )
            )

            with mock.patch(
                "lazyviewer.runtime_tree_filter.search_project_content_rg",
                return_value=({}, False, None),
            ) as search_mock:
                ops.apply_tree_filter_query("a")
                self.assertGreater(ops.loading_until, time.monotonic())
                ops.apply_tree_filter_query("ab")
                self.assertGreater(ops.loading_until, time.monotonic())
                ops.apply_tree_filter_query("a")

            self.assertEqual(search_mock.call_count, 2)
            self.assertEqual(ops.loading_until, 0.0)


if __name__ == "__main__":
    unittest.main()
