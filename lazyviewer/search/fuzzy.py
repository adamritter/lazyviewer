from __future__ import annotations

import heapq
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

from ..gitignore import get_gitignore_matcher

_PROJECT_FILES_CACHE: dict[tuple[Path, bool, bool], list[Path]] = {}
_PROJECT_FILE_LABELS_CACHE: dict[tuple[Path, bool, bool], list[str]] = {}
STRICT_SUBSTRING_ONLY_MIN_FILES = 1_000


def clear_project_files_cache() -> None:
    _PROJECT_FILES_CACHE.clear()
    _PROJECT_FILE_LABELS_CACHE.clear()


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


def _collect_project_file_labels_rg(root: Path, show_hidden: bool, skip_gitignored: bool) -> list[str] | None:
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

    labels: list[str] = []
    for raw in proc.stdout.splitlines():
        if not raw:
            continue
        labels.append(raw)
    return labels


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


def collect_project_file_labels(root: Path, show_hidden: bool, skip_gitignored: bool = False) -> list[str]:
    root = root.resolve()
    cache_key = (root, show_hidden, skip_gitignored)
    cached = _PROJECT_FILE_LABELS_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    labels = _collect_project_file_labels_rg(root, show_hidden, skip_gitignored)
    if labels is None:
        files = _collect_project_files_walk(root, show_hidden, skip_gitignored)
        labels = [to_project_relative(path, root) for path in files]

    _PROJECT_FILE_LABELS_CACHE[cache_key] = labels
    return list(labels)


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


def fuzzy_match_label_index(
    query: str,
    labels: list[str],
    labels_folded: list[str] | None = None,
    limit: int = 200,
    strict_substring_only_min_files: int = STRICT_SUBSTRING_ONLY_MIN_FILES,
) -> list[tuple[int, str, int]]:
    if labels_folded is not None and len(labels_folded) != len(labels):
        raise ValueError("labels_folded must have the same length as labels")

    max_results = max(1, limit)
    query_folded = query.casefold()

    # For very large projects, stay in strict substring mode and keep cache order.
    # This path exits as soon as we have enough matches.
    if len(labels) >= strict_substring_only_min_files:
        strict_matches: list[tuple[int, str, int]] = []
        if labels_folded is not None:
            for idx, label_folded in enumerate(labels_folded):
                match_idx = label_folded.find(query_folded)
                if match_idx < 0:
                    continue
                label = labels[idx]
                strict_matches.append((idx, label, 10_000 - (match_idx * 50) - len(label)))
                if len(strict_matches) >= max_results:
                    break
        else:
            for idx, label in enumerate(labels):
                match_idx = label.casefold().find(query_folded)
                if match_idx < 0:
                    continue
                strict_matches.append((idx, label, 10_000 - (match_idx * 50) - len(label)))
                if len(strict_matches) >= max_results:
                    break
        return strict_matches

    if labels_folded is None:
        labels_folded = [label.casefold() for label in labels]

    substring_scored: list[tuple[int, int, str, int]]

    if max_results >= len(labels):
        substring_scored = []
        for idx, label in enumerate(labels):
            match_idx = labels_folded[idx].find(query_folded)
            if match_idx < 0:
                continue
            substring_scored.append((match_idx, len(label), label, idx))
    else:
        def iter_substring_matches() -> Iterator[tuple[int, int, str, int]]:
            for idx, label in enumerate(labels):
                match_idx = labels_folded[idx].find(query_folded)
                if match_idx < 0:
                    continue
                yield (match_idx, len(label), label, idx)

        substring_scored = heapq.nsmallest(
            max_results,
            iter_substring_matches(),
            key=lambda item: (item[0], item[1], item[2]),
        )

    if substring_scored:
        if max_results >= len(labels):
            substring_scored.sort(key=lambda item: (item[0], item[1], item[2]))
        return [
            (idx, label, 10_000 - (match_idx * 50) - label_len)
            for match_idx, label_len, label, idx in substring_scored[:max_results]
        ]

    scored: list[tuple[int, int, str, int]] = []
    for idx, label in enumerate(labels):
        score = fuzzy_score(query, label)
        if score is None:
            continue
        scored.append((score, len(label), label, idx))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [(idx, label, score) for score, _, label, idx in scored[:max_results]]


def fuzzy_match_file_index(
    query: str,
    files: list[Path],
    labels: list[str],
    labels_folded: list[str] | None = None,
    limit: int = 200,
    strict_substring_only_min_files: int = STRICT_SUBSTRING_ONLY_MIN_FILES,
) -> list[tuple[Path, str, int]]:
    if len(files) != len(labels):
        raise ValueError("files and labels must have the same length")

    matched = fuzzy_match_label_index(
        query,
        labels,
        labels_folded=labels_folded,
        limit=limit,
        strict_substring_only_min_files=strict_substring_only_min_files,
    )
    return [(files[idx], label, score) for idx, label, score in matched]


def fuzzy_match_paths(
    query: str, files: list[Path], root: Path, limit: int = 200
) -> list[tuple[Path, str, int]]:
    labels = [to_project_relative(path, root) for path in files]
    return fuzzy_match_file_index(query, files, labels, limit=limit)


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
