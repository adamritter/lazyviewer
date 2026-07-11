"""Load Git changes into semantic full-file diff lines."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .model import DiffDocument, DiffLine
from .text import read_text, sanitize_text

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True, slots=True)
class _Hunk:
    new_start: int
    new_count: int
    removed: tuple[str, ...]


def _run_git(root: Path, args: list[str], timeout: float):
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _repo_root(target: Path, timeout: float) -> Path | None:
    result = _run_git(target.parent, ["rev-parse", "--show-toplevel"], timeout)
    if result is None or result.returncode != 0 or not result.stdout.strip():
        return None
    return Path(result.stdout.strip()).resolve()


def _parse_hunks(text: str) -> tuple[_Hunk, ...]:
    hunks: list[_Hunk] = []
    start = 0
    count = 0
    removed: list[str] = []
    active = False
    for line in text.splitlines():
        match = _HUNK_RE.match(line)
        if match:
            if active:
                hunks.append(_Hunk(start, count, tuple(removed)))
            start = int(match.group(3))
            count = int(match.group(4) or "1")
            removed = []
            active = True
        elif active and line.startswith("-") and not line.startswith("--- "):
            removed.append(line[1:])
    if active:
        hunks.append(_Hunk(start, count, tuple(removed)))
    return tuple(hunks)


def load_diff_document(target: Path, timeout: float = 0.2) -> DiffDocument | None:
    """Return semantic diff lines for a tracked modified file."""
    target = target.resolve()
    if not target.is_file():
        return None
    root = _repo_root(target, timeout)
    if root is None or not target.is_relative_to(root):
        return None
    relative = target.relative_to(root)
    status = _run_git(
        root,
        ["status", "--porcelain=v1", "--untracked-files=normal", "--", str(relative)],
        timeout,
    )
    status_line = "" if status is None else next((line for line in status.stdout.splitlines() if line), "")
    if status is None or status.returncode != 0 or not status_line or status_line.startswith("??"):
        return None

    diff = _run_git(root, ["diff", "--no-color", "-U0", "HEAD", "--", str(relative)], timeout)
    diff_text = diff.stdout if diff is not None and diff.returncode == 0 else ""
    if not diff_text:
        staged = _run_git(root, ["diff", "--cached", "--no-color", "-U0", "--", str(relative)], timeout)
        unstaged = _run_git(root, ["diff", "--no-color", "-U0", "--", str(relative)], timeout)
        diff_text = (
            staged.stdout if staged is not None and staged.returncode == 0 else ""
        ) or (
            unstaged.stdout if unstaged is not None and unstaged.returncode == 0 else ""
        )
    hunks = _parse_hunks(sanitize_text(diff_text))
    if not hunks:
        return None

    source_lines = sanitize_text(read_text(target)).splitlines()
    added: set[int] = set()
    removed_at: dict[int, list[str]] = {}
    for hunk in hunks:
        for line_number in range(hunk.new_start, hunk.new_start + hunk.new_count):
            if 1 <= line_number <= len(source_lines):
                added.add(line_number)
        if hunk.removed:
            insertion = max(1, min(hunk.new_start, len(source_lines) + 1))
            removed_at.setdefault(insertion, []).extend(hunk.removed)

    lines: list[DiffLine] = []
    for line_number, text in enumerate(source_lines, start=1):
        lines.extend(DiffLine("removed", removed, None) for removed in removed_at.get(line_number, []))
        lines.append(DiffLine("added" if line_number in added else "context", text, line_number))
    lines.extend(
        DiffLine("removed", removed, None)
        for removed in removed_at.get(len(source_lines) + 1, [])
    )
    return DiffDocument(target, tuple(lines))
