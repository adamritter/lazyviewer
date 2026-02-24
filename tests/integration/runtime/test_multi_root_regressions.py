"""Known multi-root regressions captured as integration tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.render import render_dual_page
from lazyviewer.render.ansi import ANSI_ESCAPE_RE
from lazyviewer.runtime import app as app_runtime


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

            def workspace_expanded_snapshot(state) -> dict[Path, set[Path]]:
                return {
                    root_path.resolve(): {expanded.resolve() for expanded in expanded_paths}
                    for root_path, expanded_paths in state.workspace_expanded.items()
                }

            def assert_invariants(state) -> None:
                roots = {root.resolve() for root in state.tree_roots}
                self.assertEqual(
                    set(state.workspace_expanded.keys()),
                    roots,
                )
                union: set[Path] = set()
                for scope_root, expanded_paths in state.workspace_expanded.items():
                    for expanded_path in expanded_paths:
                        self.assertTrue(expanded_path.resolve().is_relative_to(scope_root.resolve()))
                    union.update(expanded_paths)
                self.assertEqual(state.expanded, union)

                row_keys = []
                for entry in state.tree_entries:
                    row_keys.append(
                        (
                            entry.path.resolve(),
                            entry.depth,
                            entry.is_dir,
                            entry.workspace_root.resolve() if entry.workspace_root is not None else None,
                            entry.kind,
                            entry.line,
                            entry.column,
                        )
                    )
                self.assertEqual(len(row_keys), len(set(row_keys)))

            def assert_scope_local_toggle(
                state,
                *,
                target_scope: Path,
                before: dict[Path, set[Path]],
                after: dict[Path, set[Path]],
                toggled_path: Path,
            ) -> None:
                target_scope = target_scope.resolve()
                toggled_path = toggled_path.resolve()
                for scope_root in before:
                    if scope_root == target_scope:
                        continue
                    self.assertEqual(after[scope_root], before[scope_root])
                self.assertNotEqual(before[target_scope], after[target_scope])
                before_has = toggled_path in before[target_scope]
                after_has = toggled_path in after[target_scope]
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
                callbacks.handle_normal_key("ENTER", 120)
                after_selection = selected_scope(state)
                after_expanded = workspace_expanded_snapshot(state)
                self.assertEqual(after_selection, before_selection)
                assert_scope_local_toggle(
                    state,
                    target_scope=scope,
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

            self._run_with_fake_loop(root, fake_run_main_loop)

            self.assertIn(root, snapshots["final"])
            self.assertIn(nested, snapshots["final"])

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


if __name__ == "__main__":
    unittest.main()
