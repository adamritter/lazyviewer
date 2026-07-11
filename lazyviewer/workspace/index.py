"""Filesystem-backed file catalogs owned by the workspace component."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from pathlib import Path

from ..gitignore import get_gitignore_matcher
from .model import WorkspaceFileIndex, WorkspaceSnapshot


def _collect_labels_with_rg(root: Path, show_hidden: bool, skip_gitignored: bool) -> list[str] | None:
    if shutil.which("rg") is None:
        return None
    command = ["rg", "--files"]
    if not skip_gitignored:
        command.append("--no-ignore")
    if show_hidden:
        command.append("--hidden")
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    labels: list[str] = []
    for raw_label in completed.stdout.splitlines():
        if not raw_label:
            continue
        candidate = root / raw_label
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_relative_to(root) and resolved.is_file():
            labels.append(resolved.relative_to(root).as_posix())
    return labels


def _collect_labels_with_walk(root: Path, show_hidden: bool, skip_gitignored: bool) -> list[str]:
    labels: list[str] = []
    ignore_matcher = get_gitignore_matcher(root) if skip_gitignored else None
    for directory, dirnames, filenames in os.walk(root):
        base = Path(directory).resolve()
        if not show_hidden:
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            filenames = [name for name in filenames if not name.startswith(".")]
        if ignore_matcher is not None:
            dirnames[:] = [name for name in dirnames if not ignore_matcher.is_ignored(base / name)]
            filenames = [name for name in filenames if not ignore_matcher.is_ignored(base / name)]
        dirnames.sort(key=str.casefold)
        filenames.sort(key=str.casefold)
        for filename in filenames:
            try:
                resolved = (base / filename).resolve()
            except OSError:
                continue
            if resolved.is_relative_to(root) and resolved.is_file():
                labels.append(resolved.relative_to(root).as_posix())
    return labels


def collect_root_file_labels(root: Path, show_hidden: bool, skip_gitignored: bool) -> list[str]:
    """Collect a fresh, sorted file-label catalog for one root."""
    root = root.resolve()
    labels = _collect_labels_with_rg(root, show_hidden, skip_gitignored)
    if labels is None:
        labels = _collect_labels_with_walk(root, show_hidden, skip_gitignored)
    return sorted(labels, key=str.casefold)


def build_workspace_file_index(snapshot: WorkspaceSnapshot) -> WorkspaceFileIndex:
    """Build a file index for all roots while retaining duplicate sections."""
    roots = snapshot.query.roots
    if not roots:
        return WorkspaceFileIndex(snapshot.revision, (), (), ())

    def collect(root: Path) -> list[str]:
        return collect_root_file_labels(
            root,
            snapshot.query.show_hidden,
            snapshot.query.skip_gitignored,
        )

    if len(roots) == 1:
        labels_by_section = [collect(roots[0])]
    else:
        with ThreadPoolExecutor(
            max_workers=min(8, len(roots)),
            thread_name_prefix="lazyviewer-workspace-index",
        ) as executor:
            labels_by_section = list(executor.map(collect, roots))

    labels: list[str] = []
    paths: list[Path] = []
    sections: list[int] = []
    for section, (root, section_labels) in enumerate(zip(roots, labels_by_section)):
        for label in section_labels:
            labels.append(label)
            paths.append((root / label).resolve())
            sections.append(section)
    return WorkspaceFileIndex(
        revision=snapshot.revision,
        labels=tuple(labels),
        paths=tuple(paths),
        sections=tuple(sections),
    )


class WorkspaceIndexWarmup:
    """Latest-snapshot-wins background warmup for a workspace file index."""

    def __init__(self, load_index: Callable[[WorkspaceSnapshot], WorkspaceFileIndex]) -> None:
        self._load_index = load_index
        self._lock = threading.Lock()
        self._pending: WorkspaceSnapshot | None = None
        self._running = False

    def schedule(self, snapshot: WorkspaceSnapshot) -> None:
        with self._lock:
            self._pending = snapshot
            if self._running:
                return
            self._running = True
        threading.Thread(
            target=self._worker,
            name="lazyviewer-workspace-index",
            daemon=True,
        ).start()

    def _worker(self) -> None:
        while True:
            with self._lock:
                snapshot = self._pending
                self._pending = None
                if snapshot is None:
                    self._running = False
                    return
            try:
                self._load_index(snapshot)
            except (OSError, subprocess.SubprocessError):
                # Warmup is opportunistic; foreground loading remains available.
                continue
