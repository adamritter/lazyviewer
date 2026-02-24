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

class AppRuntimePreviewClickTestsPart1(unittest.TestCase):
    def test_single_click_identifier_in_preview_opens_content_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            file_path.write_text(
                "alpha_beta_name = first_value\n"
                "foo = alpha_beta_name + 1\n",
                encoding="utf-8",
            )
            search_calls: list[str] = []
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, query, _show_hidden, **_kwargs):
                search_calls.append(query)
                if query == "alpha_beta_name":
                    return (
                        {
                            file_path.resolve(): [
                                ContentMatch(
                                    path=file_path.resolve(),
                                    line=1,
                                    column=1,
                                    preview="alpha_beta_name = first_value",
                                ),
                                ContentMatch(
                                    path=file_path.resolve(),
                                    line=2,
                                    column=7,
                                    preview="foo = alpha_beta_name + 1",
                                )
                            ]
                        },
                        False,
                        None,
                    )
                return {}, False, None

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                right_start_col = state.left_width + 2
                target_row: int | None = None
                target_col: int | None = None

                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line).rstrip("\r\n")
                    token_start = plain.rfind("alpha_beta_name")
                    if token_start >= 0 and plain.lstrip().startswith("foo"):
                        target_row = idx + 1
                        target_col = right_start_col + token_start + len("alpha_beta")
                        break

                self.assertIsNotNone(target_row)
                self.assertIsNotNone(target_col)
                assert target_row is not None
                assert target_col is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{target_col}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{target_col}:{target_row}")

                snapshots["tree_filter_active"] = state.tree_filter_active
                snapshots["tree_filter_mode"] = state.tree_filter_mode
                snapshots["tree_filter_query"] = state.tree_filter_query
                snapshots["tree_filter_editing"] = state.tree_filter_editing
                snapshots["source_selection_anchor"] = state.source_selection_anchor
                snapshots["source_selection_focus"] = state.source_selection_focus
                entry = state.tree_entries[state.selected_idx]
                snapshots["selected_kind"] = entry.kind
                snapshots["selected_path"] = entry.path.resolve()
                snapshots["selected_line"] = entry.line
                snapshots["selected_column"] = entry.column

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch(
                "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch(
                "lazyviewer.runtime.app.collect_project_file_labels", return_value=[]
            ), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertEqual(search_calls, ["alpha_beta_name"])
            self.assertTrue(bool(snapshots["tree_filter_active"]))
            self.assertEqual(snapshots["tree_filter_mode"], "content")
            self.assertEqual(snapshots["tree_filter_query"], "alpha_beta_name")
            self.assertFalse(bool(snapshots["tree_filter_editing"]))
            self.assertIsNone(snapshots["source_selection_anchor"])
            self.assertIsNone(snapshots["source_selection_focus"])
            self.assertEqual(snapshots["selected_kind"], "search_hit")
            self.assertEqual(snapshots["selected_path"], file_path.resolve())
            self.assertEqual(snapshots["selected_line"], 2)
            self.assertEqual(snapshots["selected_column"], 7)

    def test_single_click_identifier_centers_selected_content_hit_in_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            lines = [f"line_{idx:03d} = alpha_beta_name\n" for idx in range(1, 121)]
            file_path.write_text("".join(lines), encoding="utf-8")
            snapshots: dict[str, object] = {}
            target_line = 80

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, query, _show_hidden, **_kwargs):
                if query != "alpha_beta_name":
                    return {}, False, None
                return (
                    {
                        file_path.resolve(): [
                            ContentMatch(
                                path=file_path.resolve(),
                                line=idx,
                                column=12,
                                preview=f"line_{idx:03d} = alpha_beta_name",
                            )
                            for idx in range(1, 121)
                        ]
                    },
                    False,
                    None,
                )

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                state.start = 74

                right_start_col = state.left_width + 2
                target_idx = target_line - 1
                plain = app_runtime.ANSI_ESCAPE_RE.sub("", state.lines[target_idx]).rstrip("\r\n")
                token_start = plain.find("alpha_beta_name")
                self.assertGreaterEqual(token_start, 0)
                target_row = target_idx - state.start + 1
                self.assertGreaterEqual(target_row, 1)
                self.assertLessEqual(target_row, max(1, state.usable))
                target_col = right_start_col + token_start + len("alpha")

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{target_col}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{target_col}:{target_row}")

                entry = state.tree_entries[state.selected_idx]
                snapshots["selected_kind"] = entry.kind
                snapshots["selected_path"] = entry.path.resolve()
                snapshots["selected_line"] = entry.line
                snapshots["selected_column"] = entry.column
                snapshots["selected_idx"] = state.selected_idx
                snapshots["tree_start"] = state.tree_start
                snapshots["tree_entries_len"] = len(state.tree_entries)
                snapshots["usable"] = state.usable
                snapshots["show_help"] = state.show_help
                snapshots["browser_visible"] = state.browser_visible
                snapshots["tree_filter_active"] = state.tree_filter_active
                snapshots["tree_filter_mode"] = state.tree_filter_mode
                snapshots["tree_filter_editing"] = state.tree_filter_editing
                snapshots["picker_active"] = state.picker_active

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch(
                "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch(
                "lazyviewer.runtime.app.collect_project_file_labels", return_value=[]
            ), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertEqual(snapshots["selected_kind"], "search_hit")
            self.assertEqual(snapshots["selected_path"], file_path.resolve())
            self.assertEqual(snapshots["selected_line"], target_line)
            self.assertEqual(snapshots["selected_column"], 12)

            usable = int(snapshots["usable"])
            show_help = bool(snapshots["show_help"])
            browser_visible = bool(snapshots["browser_visible"])
            tree_filter_active = bool(snapshots["tree_filter_active"])
            tree_filter_mode = str(snapshots["tree_filter_mode"])
            tree_filter_editing = bool(snapshots["tree_filter_editing"])
            picker_active = bool(snapshots["picker_active"])
            help_rows = help_panel_row_count(
                usable,
                show_help,
                browser_visible=browser_visible,
                tree_filter_active=tree_filter_active,
                tree_filter_mode=tree_filter_mode,
                tree_filter_editing=tree_filter_editing,
            )
            content_rows = max(1, usable - help_rows)
            tree_rows = max(1, content_rows - 1) if tree_filter_active and not picker_active else content_rows
            selected_idx = int(snapshots["selected_idx"])
            max_tree_start = max(0, int(snapshots["tree_entries_len"]) - tree_rows)
            expected_tree_start = max(0, selected_idx - max(1, tree_rows // 2))
            expected_tree_start = min(expected_tree_start, max_tree_start)
            self.assertEqual(int(snapshots["tree_start"]), expected_tree_start)

    def test_single_click_relative_import_module_in_preview_jumps_to_module_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_path = root / "demo.py"
            target_module = root / "mouse.py"
            file_path.write_text("from .mouse import TreeMouseHandlers\n", encoding="utf-8")
            target_module.write_text("class TreeMouseHandlers:\n    pass\n", encoding="utf-8")
            search_calls: list[str] = []
            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_search_content(_root, query, _show_hidden, **_kwargs):
                search_calls.append(query)
                return {}, False, None

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                right_start_col = state.left_width + 2
                target_row: int | None = None
                target_col: int | None = None

                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line).rstrip("\r\n")
                    token_start = plain.find("mouse")
                    if token_start >= 0 and plain.lstrip().startswith("from .mouse import"):
                        target_row = idx + 1
                        target_col = right_start_col + token_start + 1
                        break

                self.assertIsNotNone(target_row)
                self.assertIsNotNone(target_col)
                assert target_row is not None
                assert target_col is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{target_col}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{target_col}:{target_row}")

                snapshots["current_path"] = state.current_path.resolve()
                snapshots["selected_path"] = state.tree_entries[state.selected_idx].path.resolve()
                snapshots["tree_filter_active"] = state.tree_filter_active

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch(
                "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg", side_effect=fake_search_content
            ), mock.patch(
                "lazyviewer.runtime.app.collect_project_file_labels", return_value=[]
            ), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", file_path, "monokai", True, False)

            self.assertEqual(search_calls, [])
            self.assertFalse(bool(snapshots["tree_filter_active"]))
            self.assertEqual(snapshots["current_path"], target_module.resolve())
            self.assertEqual(snapshots["selected_path"], target_module.resolve())

    def test_clicking_directory_name_in_preview_selects_it_in_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            docs_dir = root / "docs"
            docs_dir.mkdir()
            (docs_dir / "guide.md").write_text("# guide\n", encoding="utf-8")
            (root / "main.py").write_text("print('x')\n", encoding="utf-8")
            snapshots: dict[str, Path] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                handle_tree_mouse_click = _callback(kwargs, "handle_tree_mouse_click")
                right_start_col = state.left_width + 2

                target_row = None
                for idx, line in enumerate(state.lines):
                    plain = app_runtime.ANSI_ESCAPE_RE.sub("", line)
                    if "docs/" in plain:
                        target_row = idx + 1
                        break
                self.assertIsNotNone(target_row)
                assert target_row is not None

                handle_tree_mouse_click(f"MOUSE_LEFT_DOWN:{right_start_col + 3}:{target_row}")
                handle_tree_mouse_click(f"MOUSE_LEFT_UP:{right_start_col + 3}:{target_row}")

                snapshots["current_path"] = state.current_path.resolve()
                snapshots["selected_path"] = state.tree_entries[state.selected_idx].path.resolve()

            with mock.patch("lazyviewer.runtime.app.run_main_loop", side_effect=fake_run_main_loop), mock.patch(
                "lazyviewer.runtime.app.TerminalController", _FakeTerminalController
            ), mock.patch("lazyviewer.runtime.app.collect_project_file_labels", return_value=[]), mock.patch(
                "lazyviewer.runtime.app.os.isatty", return_value=True
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno", return_value=0
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno", return_value=1
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden", return_value=False
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent", return_value=None
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertEqual(snapshots["current_path"], docs_dir.resolve())
            self.assertEqual(snapshots["selected_path"], docs_dir.resolve())
