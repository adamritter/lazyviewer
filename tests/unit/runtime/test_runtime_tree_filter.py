"""Tests for tree-filter runtime behavior.

Currently targets cached content-search reuse while editing queries.
Protects the no-recompute-on-backspace optimization path.
"""

from __future__ import annotations

import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.runtime.navigation import JumpLocation
from lazyviewer.search.content import ContentMatch
from lazyviewer.tree_pane.panels.filter import TreeFilterController
from lazyviewer.runtime.state import AppState
from lazyviewer.tree_model import TreeEntry


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
    def _drain_content_search(self, ops: TreeFilterController, timeout_seconds: float = 1.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            ops.poll_content_search_updates(timeout_seconds=0.01)
            if not ops.state.tree_filter_loading:
                return
        self.fail("timed out waiting for background content search to finish")

    def test_content_search_reuses_cached_results_when_backspacing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "demo.py").write_text("alpha beta gamma\n", encoding="utf-8")
            state = _make_state(root)
            state.tree_filter_active = True
            state.tree_filter_mode = "content"

            ops = TreeFilterController(
                state=state,
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
                record_jump_if_changed=lambda _origin: None,
                jump_to_path=lambda _target: None,
                jump_to_line=lambda _line: None,
            )

            with mock.patch(
                "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg",
                return_value=({}, False, None),
            ) as search_mock:
                ops.apply_tree_filter_query("a")
                self._drain_content_search(ops)
                ops.apply_tree_filter_query("ab")
                self._drain_content_search(ops)
                ops.apply_tree_filter_query("a")

            self.assertEqual(search_mock.call_count, 2)
            self.assertEqual(ops.loading_until, 0.0)

    def test_content_search_streams_partial_results_without_blocking_ui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target_file = root / "demo.py"
            target_file.write_text("alpha\nbeta\n", encoding="utf-8")
            state = _make_state(root)
            state.tree_filter_active = True
            state.tree_filter_mode = "content"

            ops = TreeFilterController(
                state=state,
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
                record_jump_if_changed=lambda _origin: None,
                jump_to_path=lambda _target: None,
                jump_to_line=lambda _line: None,
            )

            first_emitted = threading.Event()
            release_finish = threading.Event()

            def fake_streaming_search(_root, _query, _show_hidden, **kwargs):
                on_match = kwargs.get("on_match")
                should_cancel = kwargs.get("should_cancel")
                first_match = ContentMatch(path=target_file.resolve(), line=1, column=1, preview="alpha")
                second_match = ContentMatch(path=target_file.resolve(), line=2, column=1, preview="beta")
                if on_match is not None:
                    on_match(first_match.path, first_match, 1, 1)
                    first_emitted.set()
                    release_finish.wait(timeout=1.0)
                    if should_cancel is not None and should_cancel():
                        return {}, False, None
                    on_match(second_match.path, second_match, 2, 1)
                return {target_file.resolve(): [first_match, second_match]}, False, None

            with mock.patch(
                "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg",
                side_effect=fake_streaming_search,
            ):
                start = time.perf_counter()
                ops.apply_tree_filter_query("a")
                elapsed = time.perf_counter() - start
                self.assertLess(elapsed, 0.05)
                self.assertTrue(first_emitted.wait(timeout=1.0))

                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline and state.tree_filter_match_count < 1:
                    ops.poll_content_search_updates(timeout_seconds=0.01)
                self.assertEqual(state.tree_filter_match_count, 1)
                self.assertTrue(state.tree_filter_loading)

                release_finish.set()
                self._drain_content_search(ops)
                self.assertEqual(state.tree_filter_match_count, 2)
                self.assertFalse(state.tree_filter_loading)

    def test_content_search_debounces_partial_refresh_and_finishes_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target_file = root / "demo.py"
            target_file.write_text("alpha\n", encoding="utf-8")
            state = _make_state(root)
            state.tree_filter_active = True
            state.tree_filter_mode = "content"

            ops = TreeFilterController(
                state=state,
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
                record_jump_if_changed=lambda _origin: None,
                jump_to_path=lambda _target: None,
                jump_to_line=lambda _line: None,
            )

            first_emitted = threading.Event()
            release_finish = threading.Event()

            def fake_streaming_search(_root, _query, _show_hidden, **kwargs):
                on_match = kwargs.get("on_match")
                first_match = ContentMatch(path=target_file.resolve(), line=1, column=1, preview="alpha")
                if on_match is not None:
                    on_match(first_match.path, first_match, 1, 1)
                    first_emitted.set()
                    release_finish.wait(timeout=1.0)
                return {target_file.resolve(): [first_match]}, False, None

            with mock.patch(
                "lazyviewer.tree_pane.panels.filter.controller.CONTENT_SEARCH_STREAM_REFRESH_DEBOUNCE_SECONDS",
                0.05,
            ):
                with mock.patch(
                    "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg",
                    side_effect=fake_streaming_search,
                ):
                    ops.apply_tree_filter_query("a")
                    self.assertTrue(first_emitted.wait(timeout=1.0))
                    ops.poll_content_search_updates(timeout_seconds=0.01)
                    self.assertEqual(state.tree_filter_match_count, 0)
                    self.assertTrue(state.tree_filter_loading)

                    release_finish.set()
                    self._drain_content_search(ops)
                    self.assertEqual(state.tree_filter_match_count, 1)
                    self.assertFalse(state.tree_filter_loading)

    def test_content_search_click_query_hides_prompt_row_when_results_finish_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target_file = root / "demo.py"
            target_file.write_text("alpha\n", encoding="utf-8")
            state = _make_state(root)
            state.tree_filter_active = True
            state.tree_filter_mode = "content"

            ops = TreeFilterController(
                state=state,
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
                record_jump_if_changed=lambda _origin: None,
                jump_to_path=lambda _target: None,
                jump_to_line=lambda _line: None,
            )

            result = (
                {target_file.resolve(): [ContentMatch(path=target_file.resolve(), line=1, column=1, preview="alpha")]},
                False,
                None,
            )
            with mock.patch(
                "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg",
                return_value=result,
            ):
                ops.apply_tree_filter_query("alpha", debounce_prompt_row=True)
                self._drain_content_search(ops)

            self.assertFalse(state.tree_filter_prompt_row_visible)
            self.assertEqual(state.tree_filter_match_count, 1)

    def test_content_search_click_query_fast_result_skips_empty_intermediate_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target_file = root / "demo.py"
            target_file.write_text("alpha\n", encoding="utf-8")
            state = _make_state(root)
            state.tree_filter_active = True
            state.tree_filter_mode = "content"

            ops = TreeFilterController(
                state=state,
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
                record_jump_if_changed=lambda _origin: None,
                jump_to_path=lambda _target: None,
                jump_to_line=lambda _line: None,
            )

            rebuild_calls: list[dict[str, object]] = []
            original_rebuild = ops.rebuild_tree_entries

            def recording_rebuild(**kwargs) -> None:
                rebuild_calls.append(dict(kwargs))
                original_rebuild(**kwargs)

            # Instrument all internal rebuilds for this call path.
            ops.rebuild_tree_entries = recording_rebuild  # type: ignore[assignment]

            result = (
                {target_file.resolve(): [ContentMatch(path=target_file.resolve(), line=1, column=1, preview="alpha")]},
                False,
                None,
            )
            with mock.patch(
                "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg",
                return_value=result,
            ):
                ops.apply_tree_filter_query("alpha", debounce_prompt_row=True)
                self._drain_content_search(ops)

            empty_calls = [
                call
                for call in rebuild_calls
                if call.get("content_matches_override") == {}
            ]
            self.assertEqual(empty_calls, [])

    def test_content_search_click_query_reveals_prompt_row_after_delay_when_slow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "demo.py").write_text("alpha\n", encoding="utf-8")
            state = _make_state(root)
            state.tree_filter_active = True
            state.tree_filter_mode = "content"

            ops = TreeFilterController(
                state=state,
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
                record_jump_if_changed=lambda _origin: None,
                jump_to_path=lambda _target: None,
                jump_to_line=lambda _line: None,
            )

            release_finish = threading.Event()

            def fake_streaming_search(_root, _query, _show_hidden, **_kwargs):
                release_finish.wait(timeout=1.0)
                return {}, False, None

            with mock.patch(
                "lazyviewer.tree_pane.panels.filter.controller.CONTENT_SEARCH_CLICK_PROMPT_REVEAL_DELAY_SECONDS",
                0.02,
            ):
                with mock.patch(
                    "lazyviewer.tree_pane.panels.filter.controller.CONTENT_SEARCH_CLICK_INITIAL_WAIT_SECONDS",
                    0.0,
                ):
                    with mock.patch(
                        "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg",
                        side_effect=fake_streaming_search,
                    ):
                        ops.apply_tree_filter_query("alpha", debounce_prompt_row=True)
                        self.assertFalse(state.tree_filter_prompt_row_visible)
                        self.assertTrue(state.tree_filter_loading)

                        time.sleep(0.03)
                        ops.poll_content_search_updates(timeout_seconds=0.0)
                        self.assertTrue(state.tree_filter_prompt_row_visible)
                        self.assertTrue(state.tree_filter_loading)

                        release_finish.set()
                        self._drain_content_search(ops)
                        self.assertFalse(state.tree_filter_loading)

    def test_content_search_ignores_stale_results_after_query_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target_file = root / "demo.py"
            target_file.write_text("alpha\nbeta\n", encoding="utf-8")
            state = _make_state(root)
            state.tree_filter_active = True
            state.tree_filter_mode = "content"

            ops = TreeFilterController(
                state=state,
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                preview_selected_entry=lambda **_kwargs: None,
                current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
                record_jump_if_changed=lambda _origin: None,
                jump_to_path=lambda _target: None,
                jump_to_line=lambda _line: None,
            )

            first_query_started = threading.Event()
            release_first_query = threading.Event()

            def fake_streaming_search(_root, query, _show_hidden, **kwargs):
                on_match = kwargs.get("on_match")
                should_cancel = kwargs.get("should_cancel")
                if query == "a":
                    first_query_started.set()
                    if on_match is not None:
                        on_match(
                            target_file.resolve(),
                            ContentMatch(path=target_file.resolve(), line=1, column=1, preview="alpha"),
                            1,
                            1,
                        )
                    release_first_query.wait(timeout=1.0)
                    if should_cancel is not None and should_cancel():
                        return {}, False, None
                second = ContentMatch(path=target_file.resolve(), line=2, column=1, preview="beta")
                if on_match is not None:
                    on_match(second.path, second, 1, 1)
                return {target_file.resolve(): [second]}, False, None

            with mock.patch(
                "lazyviewer.tree_pane.panels.filter.matching.search_project_content_rg",
                side_effect=fake_streaming_search,
            ):
                ops.apply_tree_filter_query("a")
                self.assertTrue(first_query_started.wait(timeout=1.0))
                ops.apply_tree_filter_query("ab")
                release_first_query.set()
                self._drain_content_search(ops)

                self.assertEqual(state.tree_filter_query, "ab")
                self.assertEqual(state.tree_filter_match_count, 1)
                selected_entry = state.tree_entries[state.selected_idx]
                self.assertEqual(selected_entry.kind, "search_hit")
                self.assertEqual(selected_entry.line, 2)


if __name__ == "__main__":
    unittest.main()
