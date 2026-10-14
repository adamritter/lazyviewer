"""Shared, metadata-validated Git working-tree discovery."""
from __future__ import annotations
from collections import OrderedDict
from pathlib import Path
import os
import subprocess
_REPOSITORY_CACHE: OrderedDict[tuple, tuple[Path | None, Path | None]] = OrderedDict()

def resolve_repository(path: Path, timeout_seconds: float) -> tuple[Path | None, Path | None]:
    """Share repository discovery across files without hiding nested repositories."""
    if os.environ.get("GIT_DIR") or os.environ.get("GIT_WORK_TREE"):
        return _query_repo_and_git_dir(path, timeout_seconds)
    marker = None
    for parent in (path, *path.parents):
        candidate = parent / ".git"
        try:
            st = candidate.stat()
        except OSError:
            continue
        marker = (str(candidate), st.st_dev, st.st_ino, st.st_mtime_ns)
        break
    # Git discovery without a marker cannot yield a normal working tree.
    if marker is None:
        return None, None
    cached = _REPOSITORY_CACHE.get(marker)
    if cached is not None:
        _REPOSITORY_CACHE.move_to_end(marker)
        return cached
    result = _query_repo_and_git_dir(path, timeout_seconds)
    _REPOSITORY_CACHE[marker] = result
    while len(_REPOSITORY_CACHE) > 128:
        _REPOSITORY_CACHE.popitem(last=False)
    return result


def _query_repo_and_git_dir(path: Path, timeout_seconds: float) -> tuple[Path | None, Path | None]:
    """Resolve repository root and git-dir for a path."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel", "--git-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except Exception:
        return None, None
    if proc.returncode != 0:
        return None, None

    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None, None

    repo_root = Path(lines[0]).resolve()
    git_dir_raw = Path(lines[1])
    git_dir = git_dir_raw if git_dir_raw.is_absolute() else (repo_root / git_dir_raw)
    return repo_root, git_dir.resolve()
