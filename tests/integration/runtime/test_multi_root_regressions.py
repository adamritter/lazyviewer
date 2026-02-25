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


class AppRuntimeMultiRootRegressionTests(unittest.TestCase):
    @staticmethod
    def _run_with_fake_loop(path: Path, fake_run_main_loop) -> None:
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
            app_runtime.run_pager("", path, "monokai", True, False)

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
                        self.assertTrue(expanded_path.resolve().is_relative_to(scope_root))
                    expanded_union.update(expanded_paths)
                self.assertEqual(state.expanded, expanded_union)

                roots_set = {root_path.resolve() for root_path in roots}
                row_keys = []
                previous_section = -1
                for entry in state.tree_entries:
                    self.assertEqual(entry.kind, "path")
                    self.assertIsNotNone(entry.workspace_root)
                    self.assertIsNotNone(entry.workspace_section)
                    entry_scope = entry.workspace_root.resolve() if entry.workspace_root is not None else None
                    entry_section = entry.workspace_section
                    assert entry_section is not None
                    self.assertTrue(0 <= entry_section < len(roots))
                    self.assertEqual(roots[entry_section], entry_scope)
                    self.assertIn(entry_scope, roots_set)
                    self.assertTrue(entry.path.resolve().is_relative_to(entry_scope))
                    self.assertGreaterEqual(entry_section, previous_section)
                    previous_section = entry_section
                    row_keys.append(
                        (
                            entry.path.resolve(),
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
                rng = random.Random(1337)

                def random_dir_index(depth: int | None = None) -> int | None:
                    candidates = [
                        idx
                        for idx, entry in enumerate(state.tree_entries)
                        if entry.is_dir and (depth is None or entry.depth == depth)
                    ]
                    if not candidates:
                        return None
                    return candidates[rng.randrange(len(candidates))]

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

                def remove_random_depth0_root_and_assert() -> bool:
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

                def reroot_parent_from_random_depth0_root_and_assert_section() -> bool:
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

                def duplicate_random_depth0_root_and_assert() -> bool:
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

                def add_nested_root_then_delete_original_root_and_assert() -> bool:
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

                operations = (
                    key_toggle_random_directory,
                    mouse_toggle_random_directory,
                    add_random_directory_root_and_assert,
                    duplicate_random_depth0_root_and_assert,
                    remove_random_depth0_root_and_assert,
                    reroot_parent_from_random_depth0_root_and_assert_section,
                    reroot_selected_target_from_random_directory_and_assert_section,
                )

                assert_invariants(state)
                self.assertTrue(add_nested_root_then_delete_original_root_and_assert())
                assert_invariants(state)
                self.assertTrue(add_second_root_from_section0_child_and_assert())
                assert_invariants(state)
                self.assertTrue(reroot_key_on_nonzero_section_stays_in_same_section("R"))
                assert_invariants(state)
                self.assertTrue(reroot_key_on_nonzero_section_stays_in_same_section("r"))
                assert_invariants(state)
                executed = 4
                for _ in range(120):
                    operation = operations[rng.randrange(len(operations))]
                    if operation():
                        executed += 1
                        assert_invariants(state)
                snapshots["executed"] = executed

            self._run_with_fake_loop(root, fake_run_main_loop)
            self.assertGreater(int(snapshots["executed"]), 0)


if __name__ == "__main__":
    unittest.main()
