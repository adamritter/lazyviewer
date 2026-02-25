"""Known multi-root regressions captured as integration tests."""

from __future__ import annotations

import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from lazyviewer.render import render_dual_page
from lazyviewer.render.ansi import ANSI_ESCAPE_RE
from lazyviewer.runtime import app as app_runtime
from lazyviewer.search.content import ContentMatch
from lazyviewer.tree_pane.workspace_roots import normalized_workspace_roots


def _render_tree_left_rows(state, *, max_lines: int = 12, width: int = 140, left_width: int = 70) -> list[str]:
    """Render one frame and return visible left-pane rows (without status row)."""
    writes: list[bytes] = []
    with mock.patch(
        "lazyviewer.render.os.write",
        side_effect=lambda _fd, data: writes.append(data) or len(data),
    ):
        render_dual_page(
            text_lines=state.lines,
            text_start=state.start,
            tree_entries=state.tree_entries,
            tree_start=state.tree_start,
            tree_selected=state.selected_idx,
            max_lines=max_lines,
            current_path=state.current_path,
            tree_root=state.tree_root,
            tree_roots=state.tree_roots,
            expanded=state.tree_render_expanded,
            width=width,
            left_width=left_width,
            text_x=state.text_x,
            wrap_text=state.wrap_text,
            browser_visible=state.browser_visible,
            show_hidden=state.show_hidden,
            show_help=False,
            tree_filter_active=state.tree_filter_active,
            tree_filter_row_visible=state.tree_filter_prompt_row_visible,
            tree_filter_mode=state.tree_filter_mode,
            tree_filter_query=state.tree_filter_query,
            tree_filter_editing=state.tree_filter_editing,
            tree_filter_cursor_visible=False,
            tree_filter_match_count=state.tree_filter_match_count,
            tree_filter_truncated=state.tree_filter_truncated,
            tree_filter_loading=state.tree_filter_loading,
            tree_filter_spinner_frame=0,
            tree_filter_prefix="p>",
            tree_filter_placeholder="type to filter files",
            picker_active=state.picker_active,
            picker_mode=state.picker_mode,
            picker_query=state.picker_query,
            picker_items=state.picker_match_labels,
            picker_selected=state.picker_selected,
            picker_focus=state.picker_focus,
            picker_list_start=state.picker_list_start,
            picker_message=state.picker_message,
            git_status_overlay=state.git_status_overlay,
            tree_search_query="",
            text_search_query="",
            text_search_current_line=0,
            text_search_current_column=0,
            preview_is_git_diff=state.preview_is_git_diff,
        )
    plain = ANSI_ESCAPE_RE.sub("", b"".join(writes).decode("utf-8", errors="replace"))
    rows = [line for line in plain.split("\r\n") if line]
    content_rows = rows[:-1] if rows else []
    return [line.split("│")[0].rstrip() if "│" in line else line.rstrip() for line in content_rows]


def _render_full_frame_rows(state) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Render full frame rows (ANSI + plain) for deterministic replay checks."""
    width = max(1, int(state.left_width) + 1 + int(state.right_width))
    if not state.browser_visible:
        width = max(1, int(state.right_width))
    writes: list[bytes] = []
    with mock.patch(
        "lazyviewer.render.os.write",
        side_effect=lambda _fd, data: writes.append(data) or len(data),
    ):
        render_dual_page(
            text_lines=state.lines,
            text_start=state.start,
            tree_entries=state.tree_entries,
            tree_start=state.tree_start,
            tree_selected=state.selected_idx,
            max_lines=max(1, int(state.usable)),
            current_path=state.current_path,
            tree_root=state.tree_root,
            tree_roots=state.tree_roots,
            expanded=state.tree_render_expanded,
            width=width,
            left_width=state.left_width,
            text_x=state.text_x,
            wrap_text=state.wrap_text,
            browser_visible=state.browser_visible,
            show_hidden=state.show_hidden,
            show_help=state.show_help,
            show_tree_sizes=state.show_tree_sizes,
            status_message=state.status_message,
            tree_filter_active=state.tree_filter_active,
            tree_filter_row_visible=state.tree_filter_prompt_row_visible,
            tree_filter_mode=state.tree_filter_mode,
            tree_filter_query=state.tree_filter_query,
            tree_filter_editing=state.tree_filter_editing,
            tree_filter_cursor_visible=False,
            tree_filter_match_count=state.tree_filter_match_count,
            tree_filter_truncated=state.tree_filter_truncated,
            tree_filter_loading=state.tree_filter_loading,
            tree_filter_spinner_frame=0,
            tree_filter_prefix="p>",
            tree_filter_placeholder="type to filter files",
            picker_active=state.picker_active,
            picker_mode=state.picker_mode,
            picker_query=state.picker_query,
            picker_items=state.picker_match_labels,
            picker_selected=state.picker_selected,
            picker_focus=state.picker_focus,
            picker_list_start=state.picker_list_start,
            picker_message=state.picker_message,
            git_status_overlay=state.git_status_overlay,
            tree_search_query="",
            text_search_query="",
            text_search_current_line=0,
            text_search_current_column=0,
            preview_is_git_diff=state.preview_is_git_diff,
            source_selection_anchor=state.source_selection_anchor,
            source_selection_focus=state.source_selection_focus,
            workspace_expanded=state.workspace_expanded,
            theme=state.theme,
        )
    frame = b"".join(writes).decode("utf-8", errors="replace")
    if frame.startswith("\033[H\033[J"):
        frame = frame[len("\033[H\033[J") :]
    ansi_rows = tuple(frame.split("\r\n")) if frame else tuple()
    plain_rows = tuple(ANSI_ESCAPE_RE.sub("", row) for row in ansi_rows)
    return ansi_rows, plain_rows


class AppRuntimeMultiRootRegressionTests(unittest.TestCase):
    @staticmethod
    def _run_with_fake_loop(path: Path, fake_run_main_loop, workspace_paths: list[Path] | None = None) -> None:
        class _FakeTerminalController:
            def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                self.stdin_fd = stdin_fd
                self.stdout_fd = stdout_fd

            def supports_kitty_graphics(self) -> bool:
                return False

        with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
            "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
        ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
            "lazyviewer.runtime.app.os.isatty", return_value=True
        ), mock.patch("lazyviewer.runtime.app.sys.stdin.fileno", return_value=0), mock.patch(
            "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
        ), mock.patch("lazyviewer.runtime.app.load_show_hidden", return_value=False), mock.patch(
            "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
        ):
            app_runtime.run_pager("", path, "monokai", True, False, workspace_paths=workspace_paths)

    def test_startup_workspace_paths_seed_multiple_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            first = root / "one"
            second = root / "two"
            first.mkdir()
            second.mkdir()
            snapshots: dict[str, object] = {}

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                snapshots["roots"] = list(state.tree_roots)
                snapshots["depth0_rows"] = [
                    (entry.path.resolve(), entry.workspace_section)
                    for entry in state.tree_entries
                    if entry.is_dir and entry.depth == 0
                ]

            self._run_with_fake_loop(first, fake_run_main_loop, workspace_paths=[first, second])

            self.assertEqual(snapshots["roots"], [first.resolve(), second.resolve()])
            self.assertEqual(
                snapshots["depth0_rows"],
                [(first.resolve(), 0), (second.resolve(), 1)],
            )

    def test_multiroot_render_shows_forest_without_workspace_banner_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            (nested / "demo.py").write_text("print('nested')\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            def fake_run_main_loop(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]
                nested_idx = next(
                    idx for idx, entry in enumerate(state.tree_entries) if entry.path.resolve() == nested.resolve()
                )
                state.selected_idx = nested_idx
                callbacks.handle_normal_key("a", 120)
                snapshots["rows"] = _render_tree_left_rows(state)

            self._run_with_fake_loop(root, fake_run_main_loop)

            rows = [str(row) for row in snapshots["rows"] if str(row).strip()]
            self.assertTrue(rows)
            self.assertTrue(rows[0].lstrip().startswith(("▾", "▸")))
            self.assertFalse(any(row.strip().startswith("* ") for row in rows))

    def test_multiroot_overlapping_roots_keep_nested_directory_visible_in_parent_and_root_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            child_file = nested / "demo.py"
            child_file.write_text("print('nested')\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            def fake_run_main_loop(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]
                nested_idx = next(
                    idx for idx, entry in enumerate(state.tree_entries) if entry.path.resolve() == nested.resolve()
                )
                state.selected_idx = nested_idx
                callbacks.handle_normal_key("a", 120)

                snapshots["nested_dir_depths"] = [
                    entry.depth
                    for entry in state.tree_entries
                    if entry.is_dir and entry.path.resolve() == nested.resolve()
                ]
                snapshots["nested_file_depths"] = [
                    entry.depth
                    for entry in state.tree_entries
                    if (not entry.is_dir) and entry.path.resolve() == child_file.resolve()
                ]

            self._run_with_fake_loop(root, fake_run_main_loop)

            self.assertEqual(snapshots["nested_dir_depths"], [1, 0])
            self.assertEqual(snapshots["nested_file_depths"], [1])

    def test_multiroot_duplicate_root_selects_new_duplicate_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "demo.py").write_text("print('root')\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            def fake_run_main_loop(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]
                root_idx = next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.is_dir and entry.depth == 0 and entry.path.resolve() == root
                )
                state.selected_idx = root_idx
                callbacks.handle_normal_key("a", 120)

                snapshots["roots"] = [path.resolve() for path in state.tree_roots]
                snapshots["depth0_sections"] = [
                    entry.workspace_section
                    for entry in state.tree_entries
                    if entry.is_dir and entry.depth == 0 and entry.path.resolve() == root
                ]
                selected = state.tree_entries[state.selected_idx]
                snapshots["selected_path"] = selected.path.resolve()
                snapshots["selected_depth"] = selected.depth
                snapshots["selected_section"] = selected.workspace_section

            self._run_with_fake_loop(root, fake_run_main_loop)

            self.assertEqual(snapshots["roots"], [root, root])
            self.assertEqual(snapshots["depth0_sections"], [0, 1])
            self.assertEqual(snapshots["selected_path"], root)
            self.assertEqual(snapshots["selected_depth"], 0)
            self.assertEqual(snapshots["selected_section"], 1)

    def test_multiroot_enter_on_active_nested_root_does_not_duplicate_or_cross_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            child_file = nested / "demo.py"
            child_file.write_text("print('nested')\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            def fake_run_main_loop(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]
                nested_idx = next(
                    idx for idx, entry in enumerate(state.tree_entries) if entry.path.resolve() == nested.resolve()
                )
                state.selected_idx = nested_idx
                callbacks.handle_normal_key("a", 120)

                active_nested_idx = next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.is_dir and entry.depth == 0 and entry.path.resolve() == nested.resolve()
                )
                state.selected_idx = active_nested_idx
                snapshots["file_rows_before"] = [
                    entry.depth
                    for entry in state.tree_entries
                    if not entry.is_dir and entry.path.resolve() == child_file.resolve()
                ]

                callbacks.handle_normal_key("ENTER", 120)
                snapshots["file_rows_after_close"] = [
                    entry.depth
                    for entry in state.tree_entries
                    if not entry.is_dir and entry.path.resolve() == child_file.resolve()
                ]
                callbacks.handle_normal_key("ENTER", 120)
                snapshots["file_rows_after_reopen"] = [
                    entry.depth
                    for entry in state.tree_entries
                    if not entry.is_dir and entry.path.resolve() == child_file.resolve()
                ]

            self._run_with_fake_loop(root, fake_run_main_loop)

            self.assertEqual(snapshots["file_rows_before"], [1])
            self.assertEqual(snapshots["file_rows_after_close"], [])
            self.assertEqual(snapshots["file_rows_after_reopen"], [1])

    def test_multiroot_same_directory_path_has_independent_expand_state_per_root_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            child_file = nested / "demo.py"
            child_file.write_text("print('nested')\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            def file_depths(state) -> list[int]:
                return [
                    entry.depth
                    for entry in state.tree_entries
                    if not entry.is_dir and entry.path.resolve() == child_file.resolve()
                ]

            def find_nested_dir_index(
                state,
                *,
                depth: int,
                workspace_root: Path,
            ) -> int:
                workspace_root_resolved = workspace_root.resolve()
                return next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.is_dir
                    and entry.depth == depth
                    and entry.path.resolve() == nested.resolve()
                    and entry.workspace_root is not None
                    and entry.workspace_root.resolve() == workspace_root_resolved
                )

            def fake_run_main_loop(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]

                parent_nested_idx = find_nested_dir_index(state, depth=1, workspace_root=root)
                state.selected_idx = parent_nested_idx
                callbacks.handle_normal_key("a", 120)

                parent_nested_idx = find_nested_dir_index(state, depth=1, workspace_root=root)
                state.selected_idx = parent_nested_idx
                callbacks.handle_normal_key("ENTER", 120)
                snapshots["both_open"] = file_depths(state)

                nested_root_idx = find_nested_dir_index(state, depth=0, workspace_root=nested)
                state.selected_idx = nested_root_idx
                callbacks.handle_normal_key("ENTER", 120)
                snapshots["nested_root_closed"] = file_depths(state)

                parent_nested_idx = find_nested_dir_index(state, depth=1, workspace_root=root)
                state.selected_idx = parent_nested_idx
                callbacks.handle_normal_key("ENTER", 120)
                snapshots["both_closed"] = file_depths(state)

                nested_root_idx = find_nested_dir_index(state, depth=0, workspace_root=nested)
                state.selected_idx = nested_root_idx
                callbacks.handle_normal_key("ENTER", 120)
                snapshots["only_nested_root_open"] = file_depths(state)

            self._run_with_fake_loop(root, fake_run_main_loop)

            self.assertEqual(snapshots["both_open"], [2, 1])
            self.assertEqual(snapshots["nested_root_closed"], [2])
            self.assertEqual(snapshots["both_closed"], [])
            self.assertEqual(snapshots["only_nested_root_open"], [1])

    def test_multiroot_enter_toggle_keeps_selection_in_same_root_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            child_file = nested / "demo.py"
            child_file.write_text("print('nested')\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            def selected_scope(state) -> tuple[int, Path, Path | None]:
                entry = state.tree_entries[state.selected_idx]
                return (
                    entry.depth,
                    entry.path.resolve(),
                    entry.workspace_root.resolve() if entry.workspace_root is not None else None,
                )

            def find_nested_dir_index(
                state,
                *,
                depth: int,
                workspace_root: Path,
            ) -> int:
                workspace_root_resolved = workspace_root.resolve()
                return next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.is_dir
                    and entry.depth == depth
                    and entry.path.resolve() == nested.resolve()
                    and entry.workspace_root is not None
                    and entry.workspace_root.resolve() == workspace_root_resolved
                )

            def fake_run_main_loop(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]
                parent_nested_idx = find_nested_dir_index(state, depth=1, workspace_root=root)
                state.selected_idx = parent_nested_idx
                callbacks.handle_normal_key("a", 120)

                parent_nested_idx = find_nested_dir_index(state, depth=1, workspace_root=root)
                state.selected_idx = parent_nested_idx
                snapshots["before_parent_toggle"] = selected_scope(state)
                callbacks.handle_normal_key("ENTER", 120)
                snapshots["after_parent_toggle"] = selected_scope(state)

                nested_root_idx = find_nested_dir_index(state, depth=0, workspace_root=nested)
                state.selected_idx = nested_root_idx
                snapshots["before_nested_root_toggle"] = selected_scope(state)
                callbacks.handle_normal_key("ENTER", 120)
                snapshots["after_nested_root_toggle"] = selected_scope(state)

            self._run_with_fake_loop(root, fake_run_main_loop)

            self.assertEqual(
                snapshots["before_parent_toggle"],
                snapshots["after_parent_toggle"],
            )
            self.assertEqual(
                snapshots["before_nested_root_toggle"],
                snapshots["after_nested_root_toggle"],
            )

    def test_multiroot_toggle_sequence_preserves_section_isolation_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            child_file = nested / "demo.py"
            child_file.write_text("print('nested')\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            def find_nested_dir_index(
                state,
                *,
                depth: int,
                workspace_root: Path,
            ) -> int:
                workspace_root_resolved = workspace_root.resolve()
                return next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.is_dir
                    and entry.depth == depth
                    and entry.path.resolve() == nested.resolve()
                    and entry.workspace_root is not None
                    and entry.workspace_root.resolve() == workspace_root_resolved
                )

            def selected_scope(state) -> tuple[Path, Path | None]:
                entry = state.tree_entries[state.selected_idx]
                return (
                    entry.path.resolve(),
                    entry.workspace_root.resolve() if entry.workspace_root is not None else None,
                )

            def workspace_expanded_snapshot(state) -> list[set[Path]]:
                return [
                    {expanded.resolve() for expanded in expanded_paths}
                    for expanded_paths in state.workspace_expanded
                ]

            def assert_invariants(state) -> None:
                roots = [root.resolve() for root in state.tree_roots]
                self.assertEqual(len(state.workspace_expanded), len(roots))
                union: set[Path] = set()
                for scope_root, expanded_paths in zip(roots, state.workspace_expanded):
                    for expanded_path in expanded_paths:
                        self.assertTrue(expanded_path.resolve().is_relative_to(scope_root))
                    union.update(expanded_paths)
                self.assertEqual(state.expanded, union)

                row_keys = []
                for entry in state.tree_entries:
                    self.assertIsNotNone(entry.workspace_section)
                    assert entry.workspace_section is not None
                    self.assertTrue(0 <= entry.workspace_section < len(roots))
                    row_keys.append(
                        (
                            entry.path.resolve(),
                            entry.depth,
                            entry.is_dir,
                            entry.workspace_root.resolve() if entry.workspace_root is not None else None,
                            entry.workspace_section,
                            entry.kind,
                            entry.line,
                            entry.column,
                        )
                    )
                self.assertEqual(len(row_keys), len(set(row_keys)))

            def assert_scope_local_toggle(
                state,
                *,
                target_section: int,
                before: list[set[Path]],
                after: list[set[Path]],
                toggled_path: Path,
            ) -> None:
                toggled_path = toggled_path.resolve()
                for idx in range(len(before)):
                    if idx == target_section:
                        continue
                    self.assertEqual(after[idx], before[idx])
                self.assertNotEqual(before[target_section], after[target_section])
                before_has = toggled_path in before[target_section]
                after_has = toggled_path in after[target_section]
                self.assertNotEqual(before_has, after_has)

            def run_toggle_assertions(
                state,
                callbacks,
                *,
                depth: int,
                scope: Path,
            ) -> None:
                idx = find_nested_dir_index(state, depth=depth, workspace_root=scope)
                state.selected_idx = idx
                before_selection = selected_scope(state)
                before_expanded = workspace_expanded_snapshot(state)
                selected_entry = state.tree_entries[state.selected_idx]
                target_section = selected_entry.workspace_section
                self.assertIsNotNone(target_section)
                assert target_section is not None
                callbacks.handle_normal_key("ENTER", 120)
                after_selection = selected_scope(state)
                after_expanded = workspace_expanded_snapshot(state)
                self.assertEqual(after_selection, before_selection)
                assert_scope_local_toggle(
                    state,
                    target_section=target_section,
                    before=before_expanded,
                    after=after_expanded,
                    toggled_path=nested,
                )
                assert_invariants(state)

            def fake_run_main_loop(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]

                assert_invariants(state)

                parent_nested_idx = find_nested_dir_index(state, depth=1, workspace_root=root)
                state.selected_idx = parent_nested_idx
                callbacks.handle_normal_key("a", 120)

                assert_invariants(state)

                run_toggle_assertions(state, callbacks, depth=1, scope=root)
                run_toggle_assertions(state, callbacks, depth=1, scope=root)
                run_toggle_assertions(state, callbacks, depth=0, scope=nested)
                run_toggle_assertions(state, callbacks, depth=0, scope=nested)

                snapshots["final"] = workspace_expanded_snapshot(state)
                snapshots["final_roots"] = [root_path.resolve() for root_path in state.tree_roots]

            self._run_with_fake_loop(root, fake_run_main_loop)

            self.assertIn(root.resolve(), snapshots["final_roots"])
            self.assertIn(nested.resolve(), snapshots["final_roots"])

    def test_multiroot_mouse_arrow_on_parent_root_does_not_reintroduce_nested_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            nested = root / "nested"
            nested.mkdir()
            child_file = nested / "demo.py"
            child_file.write_text("print('nested')\n", encoding="utf-8")
            snapshots: dict[str, object] = {}

            def nested_file_depths(state) -> list[int]:
                return [
                    entry.depth
                    for entry in state.tree_entries
                    if not entry.is_dir and entry.path.resolve() == child_file.resolve()
                ]

            def fake_run_main_loop(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]
                nested_idx = next(
                    idx for idx, entry in enumerate(state.tree_entries) if entry.path.resolve() == nested.resolve()
                )
                state.selected_idx = nested_idx
                callbacks.handle_normal_key("a", 120)
                snapshots["before"] = nested_file_depths(state)

                parent_root_idx = next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.is_dir and entry.depth == 0 and entry.path.resolve() == root.resolve()
                )
                row = (parent_root_idx - state.tree_start) + 1
                col = 1 + (state.tree_entries[parent_root_idx].depth * 2)
                callbacks.tree_pane.handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{col}:{row}")
                snapshots["after_close"] = nested_file_depths(state)

                parent_root_idx = next(
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if entry.is_dir and entry.depth == 0 and entry.path.resolve() == root.resolve()
                )
                row = (parent_root_idx - state.tree_start) + 1
                col = 1 + (state.tree_entries[parent_root_idx].depth * 2)
                callbacks.tree_pane.handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{col}:{row}")
                snapshots["after_reopen"] = nested_file_depths(state)

            self._run_with_fake_loop(root, fake_run_main_loop)

            self.assertEqual(snapshots["before"], [1])
            self.assertEqual(snapshots["after_close"], [1])
            self.assertEqual(snapshots["after_reopen"], [1])

    def test_multiroot_randomized_operations_preserve_tree_invariants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for rel_path in (
                "alpha/a.py",
                "alpha/deep/a2.py",
                "beta/b.py",
                "beta/deep/b2.py",
                "gamma/c.py",
            ):
                target = root / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"print('{rel_path}')\n", encoding="utf-8")

            snapshots: dict[str, object] = {}

            def section_snapshot(state) -> list[set[Path]]:
                return [{path.resolve() for path in expanded_paths} for expanded_paths in state.workspace_expanded]

            def assert_invariants(state) -> None:
                self.assertTrue(state.tree_roots)
                self.assertTrue(state.tree_entries)
                self.assertTrue(0 <= state.selected_idx < len(state.tree_entries))

                roots = normalized_workspace_roots(state.tree_roots, state.tree_root)
                state_roots = [path.resolve() for path in state.tree_roots]
                self.assertEqual(state_roots, roots)
                depth0_entries = [entry for entry in state.tree_entries if entry.is_dir and entry.depth == 0]
                self.assertEqual(
                    [entry.path.resolve() for entry in depth0_entries],
                    roots,
                )
                self.assertEqual(len(state.workspace_expanded), len(roots))
                self.assertEqual(len(depth0_entries), len(roots))
                for section_idx, depth0_entry in enumerate(depth0_entries):
                    self.assertEqual(depth0_entry.workspace_section, section_idx)
                    self.assertIsNotNone(depth0_entry.workspace_root)
                    assert depth0_entry.workspace_root is not None
                    self.assertEqual(depth0_entry.workspace_root.resolve(), roots[section_idx])
                    self.assertEqual(depth0_entry.path.resolve(), roots[section_idx])

                expanded_union: set[Path] = set()
                for scope_root, expanded_paths in zip(roots, state.workspace_expanded):
                    for expanded_path in expanded_paths:
                        expanded_path_resolved = expanded_path.resolve()
                        self.assertTrue(expanded_path_resolved.is_relative_to(scope_root))
                        expanded_union.add(expanded_path_resolved)
                self.assertEqual(state.expanded, expanded_union)

                roots_set = set(roots)
                row_keys = []
                previous_section = -1
                for entry in state.tree_entries:
                    self.assertEqual(entry.kind, "path")
                    self.assertIsNotNone(entry.workspace_root)
                    self.assertIsNotNone(entry.workspace_section)
                    entry_scope = entry.workspace_root.resolve() if entry.workspace_root is not None else None
                    entry_path = entry.path.resolve()
                    entry_section = entry.workspace_section
                    assert entry_section is not None
                    self.assertTrue(0 <= entry_section < len(roots))
                    self.assertEqual(roots[entry_section], entry_scope)
                    self.assertIn(entry_scope, roots_set)
                    self.assertTrue(entry_path.is_relative_to(entry_scope))
                    self.assertGreaterEqual(entry_section, previous_section)
                    previous_section = entry_section
                    row_keys.append(
                        (
                            entry_path,
                            entry.depth,
                            entry.is_dir,
                            entry_scope,
                            entry_section,
                        )
                    )
                self.assertEqual(len(row_keys), len(set(row_keys)))

                current_path = state.current_path.resolve()
                self.assertTrue(any(current_path.is_relative_to(root_path) for root_path in roots))

            def fake_run_main_loop(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]
                random_seeds = (1337, 2026, 4242)
                operations_per_seed = 40
                full_invariant_cadence = 8
                max_random_tree_roots = 14
                rng = random.Random(1337)
                tree_filter_panel = callbacks.tree_pane.filter_panel
                tree_filter_controller = callbacks.tree_pane.filter
                navigation = callbacks.tree_pane.navigation

                def random_dir_index(depth: int | None = None) -> int | None:
                    candidates = [
                        idx
                        for idx, entry in enumerate(state.tree_entries)
                        if entry.is_dir and (depth is None or entry.depth == depth)
                    ]
                    if not candidates:
                        return None
                    return candidates[rng.randrange(len(candidates))]

                def random_file_index() -> int | None:
                    candidates = [idx for idx, entry in enumerate(state.tree_entries) if not entry.is_dir]
                    if not candidates:
                        return None
                    return candidates[rng.randrange(len(candidates))]

                def random_entry_index() -> int | None:
                    if not state.tree_entries:
                        return None
                    return rng.randrange(len(state.tree_entries))

                def ensure_filter_closed() -> None:
                    if state.tree_filter_active:
                        tree_filter_controller.close_tree_filter(clear_query=True, restore_origin=False)

                def assert_search_undo_roundtrip(origin_path: Path, target_path: Path) -> None:
                    if target_path == origin_path:
                        return
                    moved_back = navigation.jump_back_in_history()
                    self.assertTrue(moved_back)
                    self.assertEqual(state.current_path.resolve(), origin_path)
                    moved_forward = navigation.jump_forward_in_history()
                    self.assertTrue(moved_forward)
                    self.assertEqual(state.current_path.resolve(), target_path)

                def key_toggle_random_directory() -> bool:
                    idx = random_dir_index()
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    selected_path = selected_before.path.resolve()
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    selected_section = selected_before.workspace_section
                    before_sections = section_snapshot(state)
                    before_has = selected_path in before_sections[selected_section]
                    state.selected_idx = idx
                    callbacks.handle_normal_key("ENTER", 120)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), selected_path)
                    self.assertEqual(selected_after.workspace_section, selected_section)
                    after_sections = section_snapshot(state)
                    for section_idx in range(len(before_sections)):
                        if section_idx == selected_section:
                            continue
                        self.assertEqual(after_sections[section_idx], before_sections[section_idx])
                    after_has = selected_path in after_sections[selected_section]
                    self.assertNotEqual(after_has, before_has)
                    return True

                def mouse_toggle_random_directory() -> bool:
                    idx = random_dir_index()
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    selected_path = selected_before.path.resolve()
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    selected_section = selected_before.workspace_section
                    before_sections = section_snapshot(state)
                    before_has = selected_path in before_sections[selected_section]
                    state.selected_idx = idx
                    row = (idx - state.tree_start) + 1
                    col = 1 + (state.tree_entries[idx].depth * 2)
                    callbacks.tree_pane.handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{col}:{row}")
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), selected_path)
                    self.assertEqual(selected_after.workspace_section, selected_section)
                    after_sections = section_snapshot(state)
                    if after_sections == before_sections:
                        return False
                    for section_idx in range(len(before_sections)):
                        if section_idx == selected_section:
                            continue
                        self.assertEqual(after_sections[section_idx], before_sections[section_idx])
                    after_has = selected_path in after_sections[selected_section]
                    self.assertNotEqual(after_has, before_has)
                    return True

                def add_random_directory_root_and_assert() -> bool:
                    ensure_filter_closed()
                    if len(state.tree_roots) >= max_random_tree_roots:
                        return False
                    idx = random_dir_index()
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    new_root = selected_before.path.resolve()
                    before_tree_roots = [path.resolve() for path in state.tree_roots]
                    state.selected_idx = idx
                    callbacks.handle_normal_key("a", 120)
                    after_tree_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(len(after_tree_roots), len(before_tree_roots) + 1)
                    self.assertEqual(after_tree_roots[-1], new_root)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), new_root)
                    self.assertEqual(selected_after.depth, 0)
                    self.assertEqual(selected_after.workspace_section, len(after_tree_roots) - 1)
                    return True

                def add_random_file_parent_root_and_assert() -> bool:
                    ensure_filter_closed()
                    if len(state.tree_roots) >= max_random_tree_roots:
                        return False
                    idx = random_file_index()
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    selected_file = selected_before.path.resolve()
                    new_root = selected_file.parent.resolve()
                    before_tree_roots = [path.resolve() for path in state.tree_roots]
                    state.selected_idx = idx
                    callbacks.handle_normal_key("a", 120)
                    after_tree_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(len(after_tree_roots), len(before_tree_roots) + 1)
                    self.assertEqual(after_tree_roots[-1], new_root)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), new_root)
                    self.assertEqual(selected_after.depth, 0)
                    self.assertEqual(selected_after.workspace_section, len(after_tree_roots) - 1)
                    return True

                def remove_random_depth0_root_and_assert() -> bool:
                    ensure_filter_closed()
                    if len(state.tree_roots) <= 1:
                        return False
                    idx = random_dir_index(depth=0)
                    if idx is None:
                        return False
                    selected_entry = state.tree_entries[idx]
                    selected_root = selected_entry.path.resolve()
                    self.assertIsNotNone(selected_entry.workspace_section)
                    assert selected_entry.workspace_section is not None
                    selected_section = selected_entry.workspace_section
                    before_tree_roots = [path.resolve() for path in state.tree_roots]
                    before_counts = Counter(before_tree_roots)
                    expected_after = before_tree_roots[:selected_section] + before_tree_roots[selected_section + 1 :]

                    state.selected_idx = idx
                    callbacks.handle_normal_key("d", 120)

                    after_tree_roots = [path.resolve() for path in state.tree_roots]
                    after_counts = Counter(after_tree_roots)
                    self.assertEqual(after_tree_roots, expected_after)
                    self.assertEqual(len(after_tree_roots), len(before_tree_roots) - 1)
                    self.assertEqual(after_counts[selected_root], before_counts[selected_root] - 1)

                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertIsNotNone(selected_after.workspace_section)
                    assert selected_after.workspace_section is not None
                    self.assertTrue(0 <= selected_after.workspace_section < len(after_tree_roots))
                    self.assertTrue(
                        selected_after.path.resolve().is_relative_to(after_tree_roots[selected_after.workspace_section])
                    )
                    return True

                def remove_random_entry_section_and_assert() -> bool:
                    ensure_filter_closed()
                    if len(state.tree_roots) <= 1:
                        return False
                    idx = random_entry_index()
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    selected_section = selected_before.workspace_section
                    before_tree_roots = [path.resolve() for path in state.tree_roots]
                    expected_after = before_tree_roots[:selected_section] + before_tree_roots[selected_section + 1 :]
                    if not expected_after:
                        return False

                    state.selected_idx = idx
                    callbacks.handle_normal_key("d", 120)
                    after_tree_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_tree_roots, expected_after)
                    self.assertEqual(len(after_tree_roots), len(before_tree_roots) - 1)

                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertIsNotNone(selected_after.workspace_section)
                    assert selected_after.workspace_section is not None
                    self.assertTrue(0 <= selected_after.workspace_section < len(after_tree_roots))
                    self.assertTrue(
                        selected_after.path.resolve().is_relative_to(after_tree_roots[selected_after.workspace_section])
                    )
                    return True

                def delete_only_root_noop_and_assert() -> bool:
                    ensure_filter_closed()
                    if len(state.tree_roots) != 1:
                        return False
                    idx = random_entry_index()
                    if idx is None:
                        return False
                    before_roots = [path.resolve() for path in state.tree_roots]
                    state.selected_idx = idx
                    callbacks.handle_normal_key("d", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_roots, before_roots)
                    self.assertIn("cannot delete", state.status_message)
                    return True

                def reroot_parent_from_random_depth0_root_and_assert_section() -> bool:
                    ensure_filter_closed()
                    idx = random_dir_index(depth=0)
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    selected_root = selected_before.path.resolve()
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    before_section = selected_before.workspace_section
                    before_roots = [path.resolve() for path in state.tree_roots]
                    parent_root = selected_root.parent.resolve()
                    if parent_root == selected_root:
                        return False
                    expected_after = list(before_roots)
                    expected_after[before_section] = parent_root
                    state.selected_idx = idx
                    callbacks.handle_normal_key("R", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_roots, expected_after)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertIsNotNone(selected_after.workspace_section)
                    assert selected_after.workspace_section is not None
                    self.assertEqual(selected_after.workspace_section, before_section)
                    self.assertEqual(selected_after.path.resolve(), selected_root)
                    return True

                def reroot_selected_target_from_random_directory_and_assert_section() -> bool:
                    idx = random_dir_index()
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    selected_target = selected_before.path.resolve()
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    before_section = selected_before.workspace_section
                    before_roots = [path.resolve() for path in state.tree_roots]
                    expected_after = list(before_roots)
                    expected_after[before_section] = selected_target
                    state.selected_idx = idx
                    callbacks.handle_normal_key("r", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_roots, expected_after)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertIsNotNone(selected_after.workspace_section)
                    assert selected_after.workspace_section is not None
                    self.assertEqual(selected_after.workspace_section, before_section)
                    self.assertEqual(selected_after.path.resolve(), selected_target)
                    self.assertEqual(selected_after.depth, 0)
                    return True

                def reroot_parent_from_random_nonroot_entry_and_assert_section() -> bool:
                    ensure_filter_closed()
                    idx = next(
                        (
                            row_idx
                            for row_idx, entry in enumerate(state.tree_entries)
                            if entry.depth > 0
                        ),
                        None,
                    )
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    target_section = selected_before.workspace_section
                    selected_path = selected_before.path.resolve()
                    before_roots = [path.resolve() for path in state.tree_roots]
                    section_root = before_roots[target_section]
                    parent_root = section_root.parent.resolve()
                    if parent_root == section_root:
                        return False
                    expected_after = list(before_roots)
                    expected_after[target_section] = parent_root
                    state.selected_idx = idx
                    callbacks.handle_normal_key("R", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_roots, expected_after)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertIsNotNone(selected_after.workspace_section)
                    assert selected_after.workspace_section is not None
                    self.assertEqual(selected_after.workspace_section, target_section)
                    self.assertEqual(selected_after.path.resolve(), selected_path)
                    return True

                def reroot_selected_target_from_random_file_and_assert_section() -> bool:
                    ensure_filter_closed()
                    idx = random_file_index()
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    selected_target = selected_before.path.resolve()
                    target_root = selected_target.parent.resolve()
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    before_section = selected_before.workspace_section
                    before_roots = [path.resolve() for path in state.tree_roots]
                    expected_after = list(before_roots)
                    expected_after[before_section] = target_root
                    state.selected_idx = idx
                    callbacks.handle_normal_key("r", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_roots, expected_after)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertIsNotNone(selected_after.workspace_section)
                    assert selected_after.workspace_section is not None
                    self.assertEqual(selected_after.workspace_section, before_section)
                    self.assertEqual(selected_after.path.resolve(), selected_target)
                    return True

                def duplicate_random_depth0_root_and_assert() -> bool:
                    if len(state.tree_roots) >= max_random_tree_roots:
                        return False
                    idx = random_dir_index(depth=0)
                    if idx is None:
                        return False
                    selected_root = state.tree_entries[idx].path.resolve()
                    before_tree_roots = [path.resolve() for path in state.tree_roots]
                    before_depth0 = [
                        entry.path.resolve()
                        for entry in state.tree_entries
                        if entry.is_dir and entry.depth == 0
                    ]

                    state.selected_idx = idx
                    callbacks.handle_normal_key("a", 120)

                    after_tree_roots = [path.resolve() for path in state.tree_roots]
                    after_depth0 = [
                        entry.path.resolve()
                        for entry in state.tree_entries
                        if entry.is_dir and entry.depth == 0
                    ]
                    self.assertEqual(len(after_tree_roots), len(before_tree_roots) + 1)
                    self.assertEqual(len(after_depth0), len(before_depth0) + 1)

                    before_counts = Counter(before_tree_roots)
                    after_counts = Counter(after_tree_roots)
                    self.assertEqual(after_counts[selected_root], before_counts[selected_root] + 1)

                    before_depth0_counts = Counter(before_depth0)
                    after_depth0_counts = Counter(after_depth0)
                    self.assertEqual(
                        after_depth0_counts[selected_root],
                        before_depth0_counts[selected_root] + 1,
                    )

                    selected_entry = state.tree_entries[state.selected_idx]
                    self.assertTrue(selected_entry.is_dir)
                    self.assertEqual(selected_entry.depth, 0)
                    self.assertEqual(selected_entry.path.resolve(), selected_root)
                    self.assertIsNotNone(selected_entry.workspace_section)
                    assert selected_entry.workspace_section is not None
                    self.assertEqual(selected_entry.workspace_section, len(after_tree_roots) - 1)
                    return True

                def double_toggle_random_directory_roundtrip() -> bool:
                    ensure_filter_closed()
                    idx = random_dir_index()
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    selected_path = selected_before.path.resolve()
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    selected_section = selected_before.workspace_section
                    before_sections = section_snapshot(state)
                    before_roots = [path.resolve() for path in state.tree_roots]
                    state.selected_idx = idx
                    callbacks.handle_normal_key("ENTER", 120)
                    callbacks.handle_normal_key("ENTER", 120)
                    after_sections = section_snapshot(state)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_sections, before_sections)
                    self.assertEqual(after_roots, before_roots)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), selected_path)
                    self.assertEqual(selected_after.workspace_section, selected_section)
                    return True

                def add_then_delete_last_roundtrip_random_entry() -> bool:
                    ensure_filter_closed()
                    idx = random_entry_index()
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    add_target_root = (
                        selected_before.path.resolve()
                        if selected_before.is_dir
                        else selected_before.path.resolve().parent.resolve()
                    )
                    before_roots = [path.resolve() for path in state.tree_roots]
                    state.selected_idx = idx
                    callbacks.handle_normal_key("a", 120)
                    after_add_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(len(after_add_roots), len(before_roots) + 1)
                    self.assertEqual(after_add_roots[-1], add_target_root)
                    last_root_idx = next(
                        (
                            row_idx
                            for row_idx, entry in enumerate(state.tree_entries)
                            if entry.is_dir
                            and entry.depth == 0
                            and entry.workspace_section == len(after_add_roots) - 1
                            and entry.path.resolve() == add_target_root
                        ),
                        None,
                    )
                    if last_root_idx is None:
                        return False
                    state.selected_idx = last_root_idx
                    callbacks.handle_normal_key("d", 120)
                    after_delete_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_delete_roots, before_roots)
                    return True

                def reroot_parent_then_reroot_selected_roundtrip() -> bool:
                    ensure_filter_closed()
                    idx = random_dir_index(depth=0)
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    selected_root = selected_before.path.resolve()
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    selected_section = selected_before.workspace_section
                    before_roots = [path.resolve() for path in state.tree_roots]
                    parent_root = selected_root.parent.resolve()
                    if parent_root == selected_root:
                        return False
                    state.selected_idx = idx
                    callbacks.handle_normal_key("R", 120)
                    after_reroot_parent = [path.resolve() for path in state.tree_roots]
                    expected_after_parent = list(before_roots)
                    expected_after_parent[selected_section] = parent_root
                    self.assertEqual(after_reroot_parent, expected_after_parent)
                    callbacks.handle_normal_key("r", 120)
                    after_roundtrip = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_roundtrip, before_roots)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), selected_root)
                    self.assertEqual(selected_after.workspace_section, selected_section)
                    self.assertEqual(selected_after.depth, 0)
                    return True

                def add_nested_root_then_delete_original_root_and_assert() -> bool:
                    ensure_filter_closed()
                    original_root_entry_idx = next(
                        (
                            idx
                            for idx, entry in enumerate(state.tree_entries)
                            if entry.is_dir and entry.depth == 0 and entry.workspace_section == 0
                        ),
                        None,
                    )
                    if original_root_entry_idx is None:
                        return False
                    original_root = state.tree_entries[original_root_entry_idx].path.resolve()

                    nested_dir_idx = next(
                        (
                            idx
                            for idx, entry in enumerate(state.tree_entries)
                            if entry.is_dir
                            and entry.workspace_section == 0
                            and entry.path.resolve() != original_root
                        ),
                        None,
                    )
                    if nested_dir_idx is None:
                        return False

                    state.selected_idx = nested_dir_idx
                    callbacks.handle_normal_key("a", 120)

                    original_root_entry_idx = next(
                        (
                            idx
                            for idx, entry in enumerate(state.tree_entries)
                            if entry.is_dir
                            and entry.depth == 0
                            and entry.workspace_section == 0
                            and entry.path.resolve() == original_root
                        ),
                        None,
                    )
                    if original_root_entry_idx is None:
                        return False

                    before_tree_roots = [path.resolve() for path in state.tree_roots]
                    before_counts = Counter(before_tree_roots)
                    expected_after = before_tree_roots[1:]
                    state.selected_idx = original_root_entry_idx
                    callbacks.handle_normal_key("d", 120)

                    after_tree_roots = [path.resolve() for path in state.tree_roots]
                    after_counts = Counter(after_tree_roots)
                    self.assertEqual(after_tree_roots, expected_after)
                    self.assertEqual(after_counts[original_root], before_counts[original_root] - 1)
                    return True

                def add_second_root_from_section0_child_and_assert() -> bool:
                    ensure_filter_closed()
                    root0_idx = next(
                        (
                            idx
                            for idx, entry in enumerate(state.tree_entries)
                            if entry.is_dir and entry.depth == 0 and entry.workspace_section == 0
                        ),
                        None,
                    )
                    if root0_idx is None:
                        return False
                    root0 = state.tree_entries[root0_idx].path.resolve()
                    child_idx = next(
                        (
                            idx
                            for idx, entry in enumerate(state.tree_entries)
                            if entry.is_dir and entry.workspace_section == 0 and entry.path.resolve() != root0
                        ),
                        None,
                    )
                    if child_idx is None:
                        return False
                    child_root = state.tree_entries[child_idx].path.resolve()
                    before_roots = [path.resolve() for path in state.tree_roots]
                    state.selected_idx = child_idx
                    callbacks.handle_normal_key("a", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(len(after_roots), len(before_roots) + 1)
                    self.assertEqual(after_roots[-1], child_root)
                    return True

                def reroot_key_on_nonzero_section_stays_in_same_section(key: str) -> bool:
                    ensure_filter_closed()
                    nonzero_sections = sorted({idx for idx in range(len(state.tree_roots)) if idx > 0})
                    if not nonzero_sections:
                        return False
                    target_section = nonzero_sections[-1]
                    if key == "r":
                        idx = next(
                            (
                                row_idx
                                for row_idx, entry in enumerate(state.tree_entries)
                                if entry.is_dir and entry.workspace_section == target_section and entry.depth > 0
                            ),
                            None,
                        )
                    else:
                        idx = None
                    if idx is None:
                        idx = next(
                            (
                                row_idx
                                for row_idx, entry in enumerate(state.tree_entries)
                                if entry.is_dir and entry.depth == 0 and entry.workspace_section == target_section
                            ),
                            None,
                        )
                    if idx is None:
                        return False
                    selected_before = state.tree_entries[idx]
                    selected_path = selected_before.path.resolve()
                    before_roots = [path.resolve() for path in state.tree_roots]
                    if key == "R" and selected_path.parent == selected_path:
                        return False
                    expected_after = list(before_roots)
                    if key == "R":
                        expected_after[target_section] = selected_path.parent.resolve()
                    else:
                        expected_after[target_section] = selected_path
                    state.selected_idx = idx
                    callbacks.handle_normal_key(key, 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_roots, expected_after)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertIsNotNone(selected_after.workspace_section)
                    assert selected_after.workspace_section is not None
                    self.assertEqual(selected_after.workspace_section, target_section)
                    self.assertEqual(selected_after.path.resolve(), selected_path)
                    if key == "r":
                        self.assertEqual(selected_after.depth, 0)
                    return True

                def ensure_visible_file_in_any_section() -> bool:
                    ensure_filter_closed()
                    if random_file_index() is not None:
                        return True
                    candidate_idx = next(
                        (
                            row_idx
                            for row_idx, entry in enumerate(state.tree_entries)
                            if entry.is_dir and entry.depth > 0
                        ),
                        None,
                    )
                    if candidate_idx is None:
                        return False
                    state.selected_idx = candidate_idx
                    callbacks.handle_normal_key("ENTER", 120)
                    return random_file_index() is not None

                def ctrl_p_files_search_select_and_undo() -> bool:
                    ensure_filter_closed()
                    file_idx = random_file_index()
                    if file_idx is None:
                        return False
                    target_file = state.tree_entries[file_idx].path.resolve()
                    origin_path = state.current_path.resolve()
                    query = target_file.name

                    tree_filter_panel.toggle_mode("files")
                    self.assertTrue(state.tree_filter_active)
                    self.assertEqual(state.tree_filter_mode, "files")
                    tree_filter_controller.apply_tree_filter_query(
                        query,
                        preview_selection=False,
                        select_first_file=True,
                    )
                    target_idx = next(
                        (
                            idx
                            for idx, entry in enumerate(state.tree_entries)
                            if (not entry.is_dir) and entry.path.resolve() == target_file
                        ),
                        None,
                    )
                    if target_idx is None:
                        tree_filter_controller.close_tree_filter(clear_query=True)
                        return False
                    state.selected_idx = target_idx
                    tree_filter_panel.activate_selection()
                    self.assertFalse(state.tree_filter_active)
                    self.assertEqual(state.current_path.resolve(), target_file)
                    assert_search_undo_roundtrip(origin_path, target_file)
                    return True

                def slash_content_search_select_and_undo() -> bool:
                    ensure_filter_closed()
                    file_idx = random_file_index()
                    if file_idx is None:
                        return False
                    target_file = state.tree_entries[file_idx].path.resolve()
                    origin_path = state.current_path.resolve()

                    tree_filter_panel.toggle_mode("content")
                    self.assertTrue(state.tree_filter_active)
                    self.assertEqual(state.tree_filter_mode, "content")
                    state.tree_filter_query = target_file.name
                    tree_filter_controller.rebuild_tree_entries(
                        preferred_path=target_file,
                        force_first_file=True,
                        content_matches_override={
                            target_file: [
                                ContentMatch(
                                    path=target_file,
                                    line=1,
                                    column=1,
                                    preview=target_file.name,
                                )
                            ]
                        },
                        content_truncated_override=False,
                    )
                    hit_idx = next(
                        (
                            idx
                            for idx, entry in enumerate(state.tree_entries)
                            if entry.kind == "search_hit" and entry.path.resolve() == target_file
                        ),
                        None,
                    )
                    if hit_idx is None:
                        tree_filter_controller.close_tree_filter(clear_query=True)
                        return False
                    state.selected_idx = hit_idx
                    tree_filter_panel.activate_selection()
                    self.assertTrue(state.tree_filter_active)
                    self.assertEqual(state.tree_filter_mode, "content")
                    self.assertFalse(state.tree_filter_editing)
                    self.assertEqual(state.current_path.resolve(), target_file)
                    assert_search_undo_roundtrip(origin_path, target_file)
                    tree_filter_controller.close_tree_filter(clear_query=True)
                    self.assertFalse(state.tree_filter_active)
                    return True

                operations = (
                    key_toggle_random_directory,
                    mouse_toggle_random_directory,
                    double_toggle_random_directory_roundtrip,
                    add_random_directory_root_and_assert,
                    add_random_file_parent_root_and_assert,
                    add_then_delete_last_roundtrip_random_entry,
                    duplicate_random_depth0_root_and_assert,
                    remove_random_depth0_root_and_assert,
                    remove_random_entry_section_and_assert,
                    delete_only_root_noop_and_assert,
                    reroot_parent_from_random_depth0_root_and_assert_section,
                    reroot_parent_from_random_nonroot_entry_and_assert_section,
                    reroot_parent_then_reroot_selected_roundtrip,
                    reroot_selected_target_from_random_directory_and_assert_section,
                    reroot_selected_target_from_random_file_and_assert_section,
                )

                assert_invariants(state)
                self.assertTrue(ensure_visible_file_in_any_section())
                assert_invariants(state)
                self.assertTrue(ctrl_p_files_search_select_and_undo())
                assert_invariants(state)
                self.assertTrue(slash_content_search_select_and_undo())
                assert_invariants(state)
                self.assertTrue(add_nested_root_then_delete_original_root_and_assert())
                assert_invariants(state)
                self.assertTrue(add_second_root_from_section0_child_and_assert())
                assert_invariants(state)
                self.assertTrue(reroot_key_on_nonzero_section_stays_in_same_section("R"))
                assert_invariants(state)
                self.assertTrue(reroot_key_on_nonzero_section_stays_in_same_section("r"))
                assert_invariants(state)
                executed = 6
                successful_since_invariant = 0
                for seed in random_seeds:
                    rng = random.Random(seed)
                    for _ in range(operations_per_seed):
                        operation = operations[rng.randrange(len(operations))]
                        if operation():
                            executed += 1
                            successful_since_invariant += 1
                            self.assertTrue(state.tree_entries)
                            self.assertTrue(0 <= state.selected_idx < len(state.tree_entries))
                            self.assertEqual(len(state.workspace_expanded), len(state.tree_roots))
                            if successful_since_invariant >= full_invariant_cadence:
                                assert_invariants(state)
                                successful_since_invariant = 0
                if successful_since_invariant:
                    assert_invariants(state)
                snapshots["executed"] = executed

            self._run_with_fake_loop(root, fake_run_main_loop)
            self.assertGreater(int(snapshots["executed"]), 0)

    def test_multiroot_full_replay_is_deterministic_across_record_and_replay_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for rel_path in (
                "alpha/a.py",
                "alpha/deep/a2.py",
                "beta/b.py",
                "beta/deep/b2.py",
                "gamma/c.py",
                "gamma/deep/c2.py",
            ):
                target = root / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"print('{rel_path}')\n", encoding="utf-8")

            trace: list[dict[str, object]] = []
            snapshots: list[dict[str, object]] = []

            def section_snapshot(state) -> list[set[Path]]:
                return [{path.resolve() for path in expanded_paths} for expanded_paths in state.workspace_expanded]

            def entry_selector_for_index(state, index: int) -> dict[str, object]:
                entry = state.tree_entries[index]
                self.assertIsNotNone(entry.workspace_section)
                self.assertIsNotNone(entry.workspace_root)
                assert entry.workspace_section is not None
                assert entry.workspace_root is not None
                return {
                    "path": str(entry.path.resolve()),
                    "workspace_section": int(entry.workspace_section),
                    "workspace_root": str(entry.workspace_root.resolve()),
                    "depth": int(entry.depth),
                    "is_dir": bool(entry.is_dir),
                    "kind": str(entry.kind),
                }

            def find_index_for_selector(state, selector: dict[str, object]) -> int:
                target_path = str(selector["path"])
                target_section = int(selector["workspace_section"])
                target_workspace_root = str(selector["workspace_root"])
                target_depth = int(selector["depth"])
                target_is_dir = bool(selector["is_dir"])
                target_kind = str(selector["kind"])
                matches = [
                    idx
                    for idx, entry in enumerate(state.tree_entries)
                    if str(entry.path.resolve()) == target_path
                    and entry.workspace_section == target_section
                    and str(entry.workspace_root.resolve()) == target_workspace_root
                    and entry.depth == target_depth
                    and entry.is_dir == target_is_dir
                    and entry.kind == target_kind
                ]
                self.assertEqual(
                    len(matches),
                    1,
                    msg=f"selector did not resolve uniquely: {selector}",
                )
                return matches[0]

            def state_signature(state) -> dict[str, object]:
                frame_ansi_rows, frame_plain_rows = _render_full_frame_rows(state)
                return {
                    "tree_roots": tuple(str(path.resolve()) for path in state.tree_roots),
                    "workspace_expanded": tuple(
                        tuple(sorted(str(path.resolve()) for path in expanded_paths))
                        for expanded_paths in state.workspace_expanded
                    ),
                    "expanded": tuple(sorted(str(path.resolve()) for path in state.expanded)),
                    "selected_idx": int(state.selected_idx),
                    "selected_entry": entry_selector_for_index(state, state.selected_idx),
                    "current_path": str(state.current_path.resolve()),
                    "tree_entries": tuple(
                        (
                            str(entry.path.resolve()),
                            int(entry.depth),
                            bool(entry.is_dir),
                            int(entry.workspace_section) if entry.workspace_section is not None else None,
                            str(entry.workspace_root.resolve()) if entry.workspace_root is not None else None,
                            str(entry.kind),
                            int(entry.line) if entry.line is not None else None,
                            int(entry.column) if entry.column is not None else None,
                        )
                        for entry in state.tree_entries
                    ),
                    "tree_start": int(state.tree_start),
                    "tree_filter_active": bool(state.tree_filter_active),
                    "tree_filter_mode": str(state.tree_filter_mode),
                    "tree_filter_query": str(state.tree_filter_query),
                    "tree_filter_editing": bool(state.tree_filter_editing),
                    "frame_ansi_rows": frame_ansi_rows,
                    "frame_plain_rows": frame_plain_rows,
                }

            def assert_invariants(state) -> None:
                self.assertTrue(state.tree_roots)
                self.assertTrue(state.tree_entries)
                self.assertTrue(0 <= state.selected_idx < len(state.tree_entries))

                roots = normalized_workspace_roots(state.tree_roots, state.tree_root)
                self.assertEqual([path.resolve() for path in state.tree_roots], roots)
                self.assertEqual(len(state.workspace_expanded), len(roots))
                self.assertEqual(
                    [entry.path.resolve() for entry in state.tree_entries if entry.is_dir and entry.depth == 0],
                    roots,
                )
                for scope_root, expanded_paths in zip(roots, state.workspace_expanded):
                    for expanded_path in expanded_paths:
                        resolved_expanded = expanded_path.resolve()
                        self.assertTrue(resolved_expanded.is_relative_to(scope_root))
                        self.assertTrue(resolved_expanded.is_relative_to(root))
                self.assertEqual(
                    {path.resolve() for path in state.expanded},
                    set().union(*section_snapshot(state)),
                )

                row_keys = []
                for entry in state.tree_entries:
                    self.assertEqual(entry.kind, "path")
                    self.assertIsNotNone(entry.workspace_root)
                    self.assertIsNotNone(entry.workspace_section)
                    assert entry.workspace_root is not None
                    assert entry.workspace_section is not None
                    entry_scope = entry.workspace_root.resolve()
                    entry_path = entry.path.resolve()
                    self.assertTrue(entry_path.is_relative_to(entry_scope))
                    self.assertTrue(entry_path.is_relative_to(root))
                    row_keys.append(
                        (
                            entry_path,
                            entry.depth,
                            entry.is_dir,
                            entry.workspace_section,
                            entry_scope,
                        )
                    )
                self.assertEqual(len(row_keys), len(set(row_keys)))
                self.assertTrue(state.current_path.resolve().is_relative_to(root))

            def ensure_filter_closed(state, callbacks) -> None:
                if state.tree_filter_active:
                    callbacks.tree_pane.filter.close_tree_filter(clear_query=True, restore_origin=False)

            def assert_search_undo_roundtrip(state, callbacks, origin_path: Path, target_path: Path) -> None:
                if target_path == origin_path:
                    return
                navigation = callbacks.tree_pane.navigation
                moved_back = navigation.jump_back_in_history()
                self.assertTrue(moved_back)
                self.assertEqual(state.current_path.resolve(), origin_path)
                moved_forward = navigation.jump_forward_in_history()
                self.assertTrue(moved_forward)
                self.assertEqual(state.current_path.resolve(), target_path)

            def apply_step(state, callbacks, step: dict[str, object]) -> None:
                op = str(step["op"])
                selector = step.get("selector")
                if selector is not None:
                    assert isinstance(selector, dict)
                    state.selected_idx = find_index_for_selector(state, selector)

                if op in {"toggle_enter", "toggle_mouse", "add_root", "delete_root", "reroot_parent", "reroot_selected"}:
                    ensure_filter_closed(state, callbacks)

                if op == "toggle_enter":
                    selected_before = state.tree_entries[state.selected_idx]
                    selected_path = selected_before.path.resolve()
                    self.assertTrue(selected_before.is_dir)
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    selected_section = selected_before.workspace_section
                    before_sections = section_snapshot(state)
                    before_has = selected_path in before_sections[selected_section]
                    callbacks.handle_normal_key("ENTER", 120)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), selected_path)
                    self.assertEqual(selected_after.workspace_section, selected_section)
                    after_sections = section_snapshot(state)
                    for section_idx in range(len(before_sections)):
                        if section_idx != selected_section:
                            self.assertEqual(after_sections[section_idx], before_sections[section_idx])
                    after_has = selected_path in after_sections[selected_section]
                    self.assertNotEqual(before_has, after_has)
                    return

                if op == "toggle_mouse":
                    selected_before = state.tree_entries[state.selected_idx]
                    selected_path = selected_before.path.resolve()
                    self.assertTrue(selected_before.is_dir)
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    selected_section = selected_before.workspace_section
                    before_sections = section_snapshot(state)
                    before_has = selected_path in before_sections[selected_section]
                    row = (state.selected_idx - state.tree_start) + 1
                    col = 1 + (selected_before.depth * 2)
                    callbacks.tree_pane.handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{col}:{row}")
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), selected_path)
                    self.assertEqual(selected_after.workspace_section, selected_section)
                    after_sections = section_snapshot(state)
                    for section_idx in range(len(before_sections)):
                        if section_idx != selected_section:
                            self.assertEqual(after_sections[section_idx], before_sections[section_idx])
                    after_has = selected_path in after_sections[selected_section]
                    self.assertNotEqual(before_has, after_has)
                    return

                if op == "add_root":
                    selected_before = state.tree_entries[state.selected_idx]
                    target_root = (
                        selected_before.path.resolve()
                        if selected_before.is_dir
                        else selected_before.path.resolve().parent.resolve()
                    )
                    before_roots = [path.resolve() for path in state.tree_roots]
                    callbacks.handle_normal_key("a", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(len(after_roots), len(before_roots) + 1)
                    self.assertEqual(after_roots[-1], target_root)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), target_root)
                    self.assertEqual(selected_after.depth, 0)
                    self.assertEqual(selected_after.workspace_section, len(after_roots) - 1)
                    return

                if op == "delete_root":
                    selected_before = state.tree_entries[state.selected_idx]
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    selected_section = selected_before.workspace_section
                    before_roots = [path.resolve() for path in state.tree_roots]
                    callbacks.handle_normal_key("d", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    if len(before_roots) == 1:
                        self.assertEqual(after_roots, before_roots)
                        self.assertIn("cannot delete", state.status_message)
                    else:
                        expected_after = before_roots[:selected_section] + before_roots[selected_section + 1 :]
                        self.assertEqual(after_roots, expected_after)
                        selected_after = state.tree_entries[state.selected_idx]
                        self.assertIsNotNone(selected_after.workspace_section)
                        assert selected_after.workspace_section is not None
                        self.assertTrue(0 <= selected_after.workspace_section < len(after_roots))
                    return

                if op == "reroot_parent":
                    selected_before = state.tree_entries[state.selected_idx]
                    selected_path = selected_before.path.resolve()
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    section = selected_before.workspace_section
                    before_roots = [path.resolve() for path in state.tree_roots]
                    section_root = before_roots[section]
                    parent_root = section_root.parent.resolve()
                    expected_after = list(before_roots)
                    expected_after[section] = parent_root
                    callbacks.handle_normal_key("R", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_roots, expected_after)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), selected_path)
                    self.assertEqual(selected_after.workspace_section, section)
                    return

                if op == "reroot_selected":
                    selected_before = state.tree_entries[state.selected_idx]
                    selected_path = selected_before.path.resolve()
                    target_root = selected_path if selected_before.is_dir else selected_path.parent.resolve()
                    self.assertIsNotNone(selected_before.workspace_section)
                    assert selected_before.workspace_section is not None
                    section = selected_before.workspace_section
                    before_roots = [path.resolve() for path in state.tree_roots]
                    expected_after = list(before_roots)
                    expected_after[section] = target_root
                    callbacks.handle_normal_key("r", 120)
                    after_roots = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_roots, expected_after)
                    selected_after = state.tree_entries[state.selected_idx]
                    self.assertEqual(selected_after.path.resolve(), selected_path)
                    self.assertEqual(selected_after.workspace_section, section)
                    return

                if op == "add_delete_roundtrip":
                    selected_before = state.tree_entries[state.selected_idx]
                    target_root = (
                        selected_before.path.resolve()
                        if selected_before.is_dir
                        else selected_before.path.resolve().parent.resolve()
                    )
                    before_roots = [path.resolve() for path in state.tree_roots]
                    callbacks.handle_normal_key("a", 120)
                    after_add = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(len(after_add), len(before_roots) + 1)
                    self.assertEqual(after_add[-1], target_root)
                    last_root_idx = next(
                        idx
                        for idx, entry in enumerate(state.tree_entries)
                        if entry.is_dir
                        and entry.depth == 0
                        and entry.workspace_section == len(after_add) - 1
                        and entry.path.resolve() == target_root
                    )
                    state.selected_idx = last_root_idx
                    callbacks.handle_normal_key("d", 120)
                    after_delete = [path.resolve() for path in state.tree_roots]
                    self.assertEqual(after_delete, before_roots)
                    return

                if op == "ctrl_p_jump":
                    target_path = Path(str(step["target_path"])).resolve()
                    origin_path = state.current_path.resolve()
                    query = str(step["query"])
                    callbacks.tree_pane.filter_panel.toggle_mode("files")
                    self.assertTrue(state.tree_filter_active)
                    self.assertEqual(state.tree_filter_mode, "files")
                    callbacks.tree_pane.filter.apply_tree_filter_query(
                        query,
                        preview_selection=False,
                        select_first_file=True,
                    )
                    target_idx = next(
                        idx
                        for idx, entry in enumerate(state.tree_entries)
                        if (not entry.is_dir) and entry.path.resolve() == target_path
                    )
                    state.selected_idx = target_idx
                    callbacks.tree_pane.filter_panel.activate_selection()
                    self.assertFalse(state.tree_filter_active)
                    self.assertEqual(state.current_path.resolve(), target_path)
                    assert_search_undo_roundtrip(state, callbacks, origin_path, target_path)
                    return

                if op == "slash_jump":
                    target_path = Path(str(step["target_path"])).resolve()
                    origin_path = state.current_path.resolve()
                    query = str(step["query"])
                    callbacks.tree_pane.filter_panel.toggle_mode("content")
                    self.assertTrue(state.tree_filter_active)
                    self.assertEqual(state.tree_filter_mode, "content")
                    state.tree_filter_query = query
                    callbacks.tree_pane.filter.rebuild_tree_entries(
                        preferred_path=target_path,
                        force_first_file=True,
                        content_matches_override={
                            target_path: [
                                ContentMatch(
                                    path=target_path,
                                    line=1,
                                    column=1,
                                    preview=query,
                                )
                            ]
                        },
                        content_truncated_override=False,
                    )
                    hit_idx = next(
                        idx
                        for idx, entry in enumerate(state.tree_entries)
                        if entry.kind == "search_hit" and entry.path.resolve() == target_path
                    )
                    state.selected_idx = hit_idx
                    callbacks.tree_pane.filter_panel.activate_selection()
                    self.assertTrue(state.tree_filter_active)
                    self.assertEqual(state.tree_filter_mode, "content")
                    self.assertFalse(state.tree_filter_editing)
                    self.assertEqual(state.current_path.resolve(), target_path)
                    assert_search_undo_roundtrip(state, callbacks, origin_path, target_path)
                    callbacks.tree_pane.filter.close_tree_filter(clear_query=True, restore_origin=False)
                    self.assertFalse(state.tree_filter_active)
                    return

                raise AssertionError(f"unknown replay operation: {op}")

            def build_step(state, callbacks, rng: random.Random) -> dict[str, object] | None:
                def dir_indices() -> list[int]:
                    return [idx for idx, entry in enumerate(state.tree_entries) if entry.is_dir]

                def visible_dir_indices() -> list[int]:
                    tree_rows = callbacks.tree_pane.filter.tree_view_rows()
                    return [
                        idx
                        for idx, entry in enumerate(state.tree_entries)
                        if entry.is_dir and state.tree_start <= idx < state.tree_start + tree_rows
                    ]

                def file_indices() -> list[int]:
                    return [idx for idx, entry in enumerate(state.tree_entries) if not entry.is_dir]

                operation_name = rng.choice(
                    (
                        "toggle_enter",
                        "toggle_mouse",
                        "add_root",
                        "delete_root",
                        "reroot_parent",
                        "reroot_selected",
                        "add_delete_roundtrip",
                        "ctrl_p_jump",
                        "slash_jump",
                    )
                )

                if operation_name == "toggle_enter":
                    candidates = dir_indices()
                    if not candidates:
                        return None
                    idx = rng.choice(candidates)
                    return {"op": operation_name, "selector": entry_selector_for_index(state, idx)}

                if operation_name == "toggle_mouse":
                    candidates = visible_dir_indices()
                    if not candidates:
                        return None
                    idx = rng.choice(candidates)
                    return {"op": operation_name, "selector": entry_selector_for_index(state, idx)}

                if operation_name in {"add_root", "delete_root", "reroot_selected", "add_delete_roundtrip"}:
                    if not state.tree_entries:
                        return None
                    idx = rng.randrange(len(state.tree_entries))
                    return {"op": operation_name, "selector": entry_selector_for_index(state, idx)}

                if operation_name == "reroot_parent":
                    candidates: list[int] = []
                    roots = [path.resolve() for path in state.tree_roots]
                    for idx, entry in enumerate(state.tree_entries):
                        if entry.workspace_section is None:
                            continue
                        section = entry.workspace_section
                        if not (0 <= section < len(roots)):
                            continue
                        section_root = roots[section]
                        parent_root = section_root.parent.resolve()
                        if parent_root == section_root:
                            continue
                        if not parent_root.is_relative_to(root):
                            continue
                        candidates.append(idx)
                    if not candidates:
                        return None
                    idx = rng.choice(candidates)
                    return {"op": operation_name, "selector": entry_selector_for_index(state, idx)}

                if operation_name in {"ctrl_p_jump", "slash_jump"}:
                    files = file_indices()
                    if not files:
                        return None
                    idx = rng.choice(files)
                    target_path = state.tree_entries[idx].path.resolve()
                    query = target_path.name
                    return {
                        "op": operation_name,
                        "target_path": str(target_path),
                        "query": query,
                    }

                return None

            random_seeds = (1337, 2026, 4242)
            operations_per_seed = 40

            def fake_run_main_loop_record(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]
                assert_invariants(state)
                snapshots.append(state_signature(state))
                for seed in random_seeds:
                    rng = random.Random(seed)
                    executed = 0
                    attempts = 0
                    max_attempts = operations_per_seed * 20
                    while executed < operations_per_seed and attempts < max_attempts:
                        attempts += 1
                        step = build_step(state, callbacks, rng)
                        if step is None:
                            continue
                        apply_step(state, callbacks, step)
                        assert_invariants(state)
                        trace.append(step)
                        snapshots.append(state_signature(state))
                        executed += 1
                    self.assertEqual(executed, operations_per_seed, msg=f"seed {seed} executed {executed} steps")

            def fake_run_main_loop_replay(**kwargs) -> None:
                callbacks = kwargs["callbacks"]
                state = kwargs["state"]
                assert_invariants(state)
                self.assertTrue(snapshots)
                self.assertEqual(state_signature(state), snapshots[0], msg="initial replay state mismatch")
                for step_idx, step in enumerate(trace, start=1):
                    apply_step(state, callbacks, step)
                    assert_invariants(state)
                    actual = state_signature(state)
                    expected = snapshots[step_idx]
                    self.assertEqual(
                        actual,
                        expected,
                        msg=f"replay mismatch at step {step_idx}: {step}",
                    )

            self._run_with_fake_loop(root, fake_run_main_loop_record)
            self.assertEqual(len(trace), len(random_seeds) * operations_per_seed)
            self.assertEqual(len(snapshots), len(trace) + 1)
            self._run_with_fake_loop(root, fake_run_main_loop_replay)


if __name__ == "__main__":
    unittest.main()
