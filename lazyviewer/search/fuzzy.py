"""Pure fuzzy and substring matching for search labels."""

from __future__ import annotations

import heapq
from collections.abc import Iterator
from pathlib import Path

STRICT_SUBSTRING_ONLY_MIN_FILES = 1_000


def to_project_relative(path: Path, root: Path) -> str:
    """Convert absolute path to project-relative POSIX label when possible."""
    try:
        relative = path.resolve().relative_to(root.resolve())
        return relative.as_posix()
    except Exception:
        return path.as_posix()


def fuzzy_score(query: str, candidate: str) -> int | None:
    """Score fuzzy match quality for ``query`` against ``candidate``.

    Higher scores prefer contiguous runs and segment-boundary matches.
    Returns ``None`` when query characters cannot be found in order.
    """
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
    """Case-insensitive substring index helper used before fuzzy matching."""
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
    """Match query against labels and return ``(index, label, score)`` tuples.

    For very large label sets, this switches to strict substring mode and keeps
    input order for early-exit performance.
    """
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
            """Yield substring candidates lazily for bounded ``nsmallest`` selection."""
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
    """Match query against ``labels`` and map results back to file paths."""
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
    """Convenience matcher for path lists using project-relative labels."""
    labels = [to_project_relative(path, root) for path in files]
    return fuzzy_match_file_index(query, files, labels, limit=limit)


def fuzzy_match_labels(query: str, labels: list[str], limit: int = 200) -> list[tuple[int, str, int]]:
    """Match query against free-form labels with substring-first strategy."""
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
