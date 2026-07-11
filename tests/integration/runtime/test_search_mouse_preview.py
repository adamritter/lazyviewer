"""Search-view mouse scrolling with one synchronous coalesced preview."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from lazyviewer.runtime import app as app_runtime
from lazyviewer.tree_model import TreeEntry


class SearchMousePreviewTests(unittest.TestCase):
    def test_large_wheel_scroll_synchronously_previews_only_the_final_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            files: list[Path] = []
            for index in range(30):
                path = root / f"result_{index:02d}.py"
                path.write_text(f"value_{index} = {index}\n", encoding="utf-8")
                files.append(path.resolve())

            snapshots: dict[str, object] = {}

            class _FakeTerminalController:
                def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
                    self.stdin_fd = stdin_fd
                    self.stdout_fd = stdout_fd

                def supports_kitty_graphics(self) -> bool:
                    return False

            def fake_run_main_loop(**kwargs) -> None:
                state = kwargs["state"]
                callbacks = kwargs["callbacks"]
                state.workspace.entries = [
                    TreeEntry(
                        path=path,
                        depth=1,
                        is_dir=False,
                        kind="search_hit",
                        display=path.name,
                        line=1,
                        column=1,
                    )
                    for path in files
                ]
                state.workspace.selected = 0
                state.workspace.current_path = files[0]
                state.filter.active = True
                state.filter.mode = "content"
                state.filter.query = "value"
                state.preview.search_current_line = 1
                state.preview.search_current_column = 1

                started = time.perf_counter()
                handled = callbacks.source_pane.handle_tree_mouse_wheel(
                    "MOUSE_WHEEL_DOWN:5:10:20"
                )
                snapshots["handler_elapsed"] = time.perf_counter() - started
                snapshots["handled"] = handled
                snapshots["selected_after_input"] = state.workspace.selected
                snapshots["path_after_input"] = state.workspace.current_path
                snapshots["rendered"] = state.preview.rendered

            with mock.patch(
                "lazyviewer.runtime.app.run_main_loop",
                side_effect=fake_run_main_loop,
            ), mock.patch(
                "lazyviewer.runtime.app.TerminalController",
                _FakeTerminalController,
            ), mock.patch(
                "lazyviewer.runtime.app.WorkspaceIndexWarmup.schedule",
                return_value=None,
            ), mock.patch(
                "lazyviewer.runtime.app.os.isatty",
                return_value=True,
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdin.fileno",
                return_value=0,
            ), mock.patch(
                "lazyviewer.runtime.app.sys.stdout.fileno",
                return_value=1,
            ), mock.patch(
                "lazyviewer.runtime.app.load_show_hidden",
                return_value=False,
            ), mock.patch(
                "lazyviewer.runtime.app.load_left_pane_percent",
                return_value=None,
            ), mock.patch(
                "lazyviewer.runtime.app.load_content_search_left_pane_percent",
                return_value=None,
            ), mock.patch(
                "lazyviewer.runtime.app.shutil.get_terminal_size",
                return_value=os.terminal_size((120, 24)),
            ):
                app_runtime.run_pager("", root, "monokai", True, False)

            self.assertTrue(snapshots["handled"])
            self.assertEqual(snapshots["selected_after_input"], 20)
            self.assertEqual(snapshots["path_after_input"], files[20])
            self.assertLess(float(snapshots["handler_elapsed"]), 0.05)
            self.assertIn("value_20 = 20", str(snapshots["rendered"]))


if __name__ == "__main__":
    unittest.main()
