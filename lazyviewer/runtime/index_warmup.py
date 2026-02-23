"""Background index warmup scheduler for tree filtering."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from ..state import AppState


class TreeFilterIndexWarmupScheduler:
    """Serialize best-effort background warming of file-label indexes.

    Multiple schedule requests collapse to the newest pending root/visibility
    tuple so warmup work does not pile up behind stale requests.
    """

    def __init__(
        self,
        collect_project_file_labels: Callable[..., object],
        skip_gitignored_for_hidden_mode: Callable[[bool], bool],
    ) -> None:
        """Create a scheduler backed by one daemon worker thread at a time."""
        self._collect_project_file_labels = collect_project_file_labels
        self._skip_gitignored_for_hidden_mode = skip_gitignored_for_hidden_mode
        self._lock = threading.Lock()
        self._pending: tuple[Path, bool] | None = None
        self._running = False

    def _worker(self) -> None:
        """Drain pending warmup requests until queue is empty."""
        while True:
            with self._lock:
                pending = self._pending
                self._pending = None
                if pending is None:
                    self._running = False
                    return

            root, show_hidden = pending
            try:
                self._collect_project_file_labels(
                    root,
                    show_hidden,
                    skip_gitignored=self._skip_gitignored_for_hidden_mode(show_hidden),
                )
            except Exception:
                # Warming is best-effort; foreground path still loads synchronously if needed.
                pass

    def schedule(self, root: Path, show_hidden: bool) -> None:
        """Queue a warmup request and start worker if idle."""
        with self._lock:
            self._pending = (root.resolve(), show_hidden)
            if self._running:
                return
            self._running = True

        worker = threading.Thread(
            target=self._worker,
            name="lazyviewer-file-index",
            daemon=True,
        )
        worker.start()

    def schedule_for_state(
        self,
        state: AppState,
        root: Path | None = None,
        show_hidden_value: bool | None = None,
    ) -> None:
        """Schedule warmup using explicit values or current ``AppState`` fields."""
        target_root = state.tree_root if root is None else root
        target_show_hidden = state.show_hidden if show_hidden_value is None else show_hidden_value
        self.schedule(target_root, target_show_hidden)
