from __future__ import annotations

import subprocess
from pathlib import Path

GIT_STATUS_CHANGED = 1
GIT_STATUS_UNTRACKED = 2


def _merge_flags(overlay: dict[Path, int], target: Path, flags: int) -> None:
    overlay[target] = overlay.get(target, 0) | flags


def _iter_porcelain_records(output: str) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    tokens = output.split("\0")
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        if len(token) < 4 or token[2] != " ":
            continue

        status = token[:2]
        path_text = token[3:]
        records.append((status, path_text))

        # For renamed/copied entries, porcelain -z appends an extra token
        # containing the source path; the first path token is the destination.
        if "R" in status or "C" in status:
            index += 1

    return records


def collect_git_status_overlay(tree_root: Path, timeout_seconds: float = 0.25) -> dict[Path, int]:
    tree_root = tree_root.resolve()
    try:
        root_proc = subprocess.run(
            ["git", "-C", str(tree_root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except Exception:
        return {}

    if root_proc.returncode != 0:
        return {}

    repo_root_text = root_proc.stdout.strip()
    if not repo_root_text:
        return {}
    repo_root = Path(repo_root_text).resolve()

    try:
        status_proc = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain=v1", "-z", "--untracked-files=normal"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except Exception:
        return {}

    if status_proc.returncode != 0:
        return {}

    overlay: dict[Path, int] = {}
    for status, rel_path in _iter_porcelain_records(status_proc.stdout):
        if not rel_path or status == "!!":
            continue

        flags = GIT_STATUS_UNTRACKED if status == "??" else GIT_STATUS_CHANGED
        target = (repo_root / rel_path).resolve()
        if not target.is_relative_to(tree_root):
            continue

        _merge_flags(overlay, target, flags)

        parent = target.parent
        while parent.is_relative_to(tree_root):
            _merge_flags(overlay, parent, flags)
            if parent == tree_root:
                break
            next_parent = parent.parent.resolve()
            if next_parent == parent:
                break
            parent = next_parent

    return overlay
