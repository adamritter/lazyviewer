from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .gitignore import get_gitignore_matcher

_PROJECT_FILES_CACHE: dict[tuple[Path, bool, bool], list[Path]] = {}


def clear_project_files_cache() -> None:
    _PROJECT_FILES_CACHE.clear()


def _collect_project_files_walk(root: Path, show_hidden: bool, skip_gitignored: bool) -> list[Path]:
    files: list[Path] = []
    ignore_matcher = get_gitignore_matcher(root) if skip_gitignored else None
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath).resolve()
        if not show_hidden:
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            filenames = [name for name in filenames if not name.startswith(".")]
        if ignore_matcher is not None:
            dirnames[:] = [name for name in dirnames if not ignore_matcher.is_ignored(base / name)]
            filenames = [name for name in filenames if not ignore_matcher.is_ignored(base / name)]
        dirnames.sort(key=str.lower)
        filenames.sort(key=str.lower)
        for filename in filenames:
            path = (base / filename).resolve()
            if path.is_file():
                files.append(path)
    return files


def _collect_project_files_rg(root: Path, show_hidden: bool, skip_gitignored: bool) -> list[Path] | None:
    if shutil.which("rg") is None:
        return None

    cmd = ["rg", "--files"]
    if not skip_gitignored:
        cmd.append("--no-ignore")
    if show_hidden:
        cmd.append("--hidden")

    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
    except Exception:
        return None

    files: list[Path] = []
    for raw in proc.stdout.splitlines():
        if not raw:
            continue
        path = (root / raw).resolve()
        try:
            relative_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if not show_hidden and any(part.startswith(".") for part in relative_parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def collect_project_files(root: Path, show_hidden: bool, skip_gitignored: bool = False) -> list[Path]:
    root = root.resolve()
    cache_key = (root, show_hidden, skip_gitignored)
    cached = _PROJECT_FILES_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    files = _collect_project_files_rg(root, show_hidden, skip_gitignored)
    if files is None:
        files = _collect_project_files_walk(root, show_hidden, skip_gitignored)

    files = sorted(files, key=lambda p: to_project_relative(p, root).casefold())
    _PROJECT_FILES_CACHE[cache_key] = files
    return list(files)


def to_project_relative(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
        return relative.as_posix()
    except Exception:
        return path.as_posix()


def fuzzy_score(query: str, candidate: str) -> int | None:
    if not query:
        return 0
    query_folded = query.casefold()
    candidate_folded = candidate.casefold()

    score = 0
    prev_idx = -1
    run = 0
    for needle in query_folded:
        idx = candidate_folded.find(needle, prev_idx + 1)
        if idx < 0:
            return None
        if idx == prev_idx + 1:
            run += 1
            score += 20 + min(16, run * 4)
        else:
            gap = idx - prev_idx - 1
            run = 0
            score -= min(40, gap * 2)
        if idx == 0 or candidate_folded[idx - 1] in "/_- .":
            score += 35
        prev_idx = idx

    score -= len(candidate_folded) // 5
    return score


def substring_index(query: str, candidate: str) -> int | None:
    if not query:
        return 0
    idx = candidate.casefold().find(query.casefold())
    if idx < 0:
        return None
    return idx


def fuzzy_match_paths(
    query: str, files: list[Path], root: Path, limit: int = 200
) -> list[tuple[Path, str, int]]:
    substring_scored: list[tuple[int, int, str, Path]] = []
    for path in files:
        label = to_project_relative(path, root)
        idx = substring_index(query, label)
        if idx is None:
            continue
        substring_scored.append((idx, len(label), label, path))
    if substring_scored:
        substring_scored.sort(key=lambda item: (item[0], item[1], item[2]))
        return [
            (path, label, 10_000 - (idx * 50) - label_len)
            for idx, label_len, label, path in substring_scored[: max(1, limit)]
        ]

    scored: list[tuple[int, int, str, Path]] = []
    for path in files:
        label = to_project_relative(path, root)
        score = fuzzy_score(query, label)
        if score is None:
            continue
        scored.append((score, len(label), label, path))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [(path, label, score) for score, _, label, path in scored[: max(1, limit)]]


def fuzzy_match_labels(query: str, labels: list[str], limit: int = 200) -> list[tuple[int, str, int]]:
    substring_scored: list[tuple[int, int, str, int]] = []
    for idx, label in enumerate(labels):
        substr_idx = substring_index(query, label)
        if substr_idx is None:
            continue
        substring_scored.append((substr_idx, len(label), label, idx))
    if substring_scored:
        substring_scored.sort(key=lambda item: (item[0], item[1], item[2]))
        return [
            (label_idx, label, 10_000 - (substr_idx * 50) - label_len)
            for substr_idx, label_len, label, label_idx in substring_scored[: max(1, limit)]
        ]

    scored: list[tuple[int, int, str, int]] = []
    for idx, label in enumerate(labels):
        score = fuzzy_score(query, label)
        if score is None:
            continue
        scored.append((score, len(label), label, idx))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [(idx, label, score) for score, _, label, idx in scored[: max(1, limit)]]
