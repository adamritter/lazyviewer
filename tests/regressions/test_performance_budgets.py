"""Performance budget tests for startup, scrolling, and search paths.

These tests use synthetic large inputs with conservative time budgets so
regressions are caught without depending on machine-specific microbenchmarks.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.source_pane.diff import _ADDED_BG_SGR, _REMOVED_BG_SGR, _apply_line_background
from lazyviewer.runtime.navigation import JumpLocation
from lazyviewer.render import render_dual_page
from lazyviewer.tree_pane.panels.filter import TreeFilterOps
from lazyviewer.search.fuzzy import (
    clear_project_files_cache,
    collect_project_file_labels,
    fuzzy_match_label_index,
)
from lazyviewer.runtime.state import AppState
from lazyviewer.tree_model import TreeEntry, build_tree_entries


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


class PerformanceBudgetTests(unittest.TestCase):
    def test_startup_budget_large_repo_tree_and_index_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            expanded = {root}
            file_count = 0
            for d_idx in range(50):
                subdir = root / f"pkg_{d_idx:03d}"
                subdir.mkdir()
                expanded.add(subdir.resolve())
                for f_idx in range(50):
                    (subdir / f"file_{f_idx:03d}.py").write_text("x = 1\n", encoding="utf-8")
                    file_count += 1

            clear_project_files_cache()
            with mock.patch("lazyviewer.search.fuzzy.shutil.which", return_value=None):
                start = time.perf_counter()
                entries = build_tree_entries(
                    root=root,
                    expanded=expanded,
                    show_hidden=False,
                    skip_gitignored=False,
                )
                labels = collect_project_file_labels(
                    root,
                    show_hidden=False,
                    skip_gitignored=False,
                )
                elapsed = time.perf_counter() - start

            self.assertGreaterEqual(len(entries), file_count + 1)
            self.assertEqual(len(labels), file_count)
            self.assertLess(elapsed, 1.0, f"startup budget exceeded: {elapsed:.3f}s")

    def test_scroll_budget_large_diff_frame_render(self) -> None:
        line_count = 20_000
        diff_lines: list[str] = []
        source_lines: list[str] = []
        for idx in range(line_count):
            source_line = f"value_{idx:05d} = {idx}\n"
            source_lines.append(source_line)
            rendered_line = source_line.rstrip("\n")
            if idx % 9 == 0:
                diff_lines.append(_apply_line_background(rendered_line, _ADDED_BG_SGR))
            elif idx % 13 == 0:
                diff_lines.append(_apply_line_background(rendered_line, _REMOVED_BG_SGR))
            else:
                diff_lines.append(f"  {rendered_line}")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text("".join(source_lines), encoding="utf-8")
            writes: list[bytes] = []
            with mock.patch("lazyviewer.render.os.write", side_effect=lambda _fd, data: writes.append(data) or len(data)):
                start = time.perf_counter()
                render_dual_page(
                    text_lines=diff_lines,
                    text_start=line_count // 2,
                    tree_entries=[],
                    tree_start=0,
                    tree_selected=0,
                    max_lines=48,
                    current_path=path,
                    tree_root=path.parent,
                    expanded=set(),
                    width=180,
                    left_width=40,
                    text_x=32,
                    wrap_text=False,
                    browser_visible=False,
                    show_hidden=False,
                    preview_is_git_diff=True,
                )
                elapsed = time.perf_counter() - start

        self.assertTrue(writes)
        self.assertLess(elapsed, 0.25, f"diff scroll render budget exceeded: {elapsed:.3f}s")

    def test_search_budget_large_label_set(self) -> None:
        labels = [f"src/module_{idx:05d}/service_{idx % 113:03d}.py" for idx in range(60_000)]
        start = time.perf_counter()
        matches = fuzzy_match_label_index("service_042", labels, limit=2_000)
        elapsed = time.perf_counter() - start

        self.assertGreater(len(matches), 0)
        self.assertLess(elapsed, 0.25, f"search budget exceeded: {elapsed:.3f}s")

    def test_content_search_cache_hit_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "demo.py").write_text("alpha beta gamma\n", encoding="utf-8")
            state = _make_state(root)
            state.tree_filter_active = True
            state.tree_filter_mode = "content"

            ops = TreeFilterOps(
                state=state,
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
                record_jump_if_changed=lambda _origin: None,
                jump_to_path=lambda _target: None,
                jump_to_line=lambda _line: None,
            )

            def slow_search(*_args, **_kwargs):
                time.sleep(0.07)
                return {}, False, None

            with mock.patch("lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg", side_effect=slow_search):
                cold_start = time.perf_counter()
                ops.apply_tree_filter_query("alpha")
                cold_elapsed = time.perf_counter() - cold_start

                warm_start = time.perf_counter()
                ops.apply_tree_filter_query("alpha")
                warm_elapsed = time.perf_counter() - warm_start

            self.assertGreaterEqual(cold_elapsed, 0.06)
            self.assertLess(warm_elapsed, 0.03, f"cached search budget exceeded: {warm_elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
