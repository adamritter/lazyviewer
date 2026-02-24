"""Preview-selection behavior for tree-pane navigation."""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from lazyviewer.runtime.state import AppState
from lazyviewer.tree_model import TreeEntry
from lazyviewer.tree_pane.sync import PreviewSelection


def _make_state(root: Path, entries: list[TreeEntry], selected_idx: int) -> AppState:
    resolved_root = root.resolve()
    return AppState(
        current_path=resolved_root,
        tree_root=resolved_root,
        expanded={resolved_root},
        show_hidden=False,
        tree_entries=entries,
        selected_idx=selected_idx,
        rendered="",
        lines=[""],
        start=0,
        tree_start=0,
        text_x=0,
        wrap_text=False,
        left_width=30,
        right_width=90,
        usable=24,
        max_start=0,
        last_right_width=90,
    )


class PreviewSelectionTests(unittest.TestCase):
    def test_directory_selection_requests_async_preview_without_blocking(self) -> None:
        root = Path("/tmp/lazyviewer-preview-root")
        target_dir = root / "pkg"
        entries = [
            TreeEntry(path=root, depth=0, is_dir=True),
            TreeEntry(path=target_dir, depth=1, is_dir=True),
        ]
        state = _make_state(root, entries, selected_idx=1)
        refresh_calls: list[dict[str, bool]] = []
        async_calls: list[tuple[Path, bool, bool]] = []

        def slow_refresh_rendered_for_current_path(**kwargs: bool) -> None:
            time.sleep(0.08)
            refresh_calls.append(kwargs)

        selection = PreviewSelection(
            state=state,
            clear_source_selection=lambda: False,
            refresh_rendered_for_current_path=slow_refresh_rendered_for_current_path,
            request_directory_preview_async=lambda target, **kwargs: async_calls.append(
                (target.resolve(), bool(kwargs.get("reset_scroll")), bool(kwargs.get("reset_dir_budget")))
            ),
        )

        start = time.perf_counter()
        selection.preview_selected_entry()
        elapsed = time.perf_counter() - start

        self.assertLess(elapsed, 0.03, f"directory selection blocked: {elapsed:.3f}s")
        self.assertEqual(refresh_calls, [])
        self.assertEqual(
            async_calls,
            [(target_dir.resolve(), True, True)],
        )
        self.assertEqual(state.current_path.resolve(), target_dir.resolve())

    def test_directory_selection_force_uses_sync_refresh(self) -> None:
        root = Path("/tmp/lazyviewer-preview-root")
        target_dir = root / "pkg"
        entries = [
            TreeEntry(path=root, depth=0, is_dir=True),
            TreeEntry(path=target_dir, depth=1, is_dir=True),
        ]
        state = _make_state(root, entries, selected_idx=1)
        refresh_calls: list[dict[str, bool]] = []
        async_calls: list[Path] = []

        selection = PreviewSelection(
            state=state,
            clear_source_selection=lambda: False,
            refresh_rendered_for_current_path=lambda **kwargs: refresh_calls.append(kwargs),
            request_directory_preview_async=lambda target, **_kwargs: async_calls.append(target.resolve()),
        )

        selection.preview_selected_entry(force=True)

        self.assertEqual(async_calls, [])
        self.assertEqual(refresh_calls, [{"reset_scroll": True, "reset_dir_budget": True}])
        self.assertEqual(state.current_path.resolve(), target_dir.resolve())

    def test_file_selection_still_uses_sync_refresh(self) -> None:
        root = Path("/tmp/lazyviewer-preview-root")
        target_file = root / "pkg" / "demo.py"
        entries = [
            TreeEntry(path=root, depth=0, is_dir=True),
            TreeEntry(path=target_file, depth=1, is_dir=False),
        ]
        state = _make_state(root, entries, selected_idx=1)
        refresh_calls: list[dict[str, bool]] = []
        async_calls: list[Path] = []

        selection = PreviewSelection(
            state=state,
            clear_source_selection=lambda: False,
            refresh_rendered_for_current_path=lambda **kwargs: refresh_calls.append(kwargs),
            request_directory_preview_async=lambda target, **_kwargs: async_calls.append(target.resolve()),
        )

        selection.preview_selected_entry()

        self.assertEqual(async_calls, [])
        self.assertEqual(refresh_calls, [{"reset_scroll": True, "reset_dir_budget": True}])
        self.assertEqual(state.current_path.resolve(), target_file.resolve())


if __name__ == "__main__":
    unittest.main()
