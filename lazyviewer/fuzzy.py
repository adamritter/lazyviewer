from __future__ import annotations

import os
from pathlib import Path


def collect_project_files(root: Path, show_hidden: bool) -> list[Path]:
    root = root.resolve()
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if not show_hidden:
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            filenames = [name for name in filenames if not name.startswith(".")]
        dirnames.sort(key=str.lower)
        filenames.sort(key=str.lower)
        base = Path(dirpath)
        for filename in filenames:
            path = (base / filename).resolve()
            if path.is_file():
                files.append(path)
    return files


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
