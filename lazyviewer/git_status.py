"""Git status overlay collection and badge formatting helpers.

This module translates porcelain git status output into compact per-path flags
used by the tree UI. File flags are propagated to ancestor directories under
the active tree root so collapsed folders still surface modified/untracked
state in badge form.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

from .ui_theme import DEFAULT_THEME, UITheme

GIT_STATUS_CHANGED = 1
GIT_STATUS_UNTRACKED = 2


def _merge_flags(overlay: dict[Path, int], target: Path, flags: int) -> None:
    """OR git-status flags into ``overlay[target]``."""
    overlay[target] = overlay.get(target, 0) | flags


def format_git_status_badges(
    path: Path,
    git_status_overlay: dict[Path, int] | None,
    *,
    theme: UITheme | None = None,
) -> str:
    """Render ANSI-colored status badges for a tree entry path."""
    active_theme = theme or DEFAULT_THEME
    if not git_status_overlay:
        return ""

    flags = git_status_overlay.get(path.resolve(), 0)
    if flags == 0:
        return ""

    badges: list[str] = []
    if flags & GIT_STATUS_CHANGED:
        badges.append(f"{active_theme.git_badge_changed}[M]{active_theme.reset}")
    if flags & GIT_STATUS_UNTRACKED:
        badges.append(f"{active_theme.git_badge_untracked}[?]{active_theme.reset}")
    if not badges:
        return ""
    return " " + "".join(badges)


def _resolve_repo_and_git_dir(path: Path, timeout_seconds: float) -> tuple[Path | None, Path | None]:
    """Resolve repo root and git-dir for a filesystem path."""
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


def _run_git(repo_root: Path, args: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str] | None:
    """Run a git command and return ``None`` on execution failure."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except Exception:
        return None


def _iter_porcelain_records(output: str) -> list[tuple[str, str]]:
    """Parse ``git status --porcelain=v1 -z`` records.

    Returns ``(status, path)`` pairs and skips malformed entries. For rename/copy
    records, the extra source-path token is consumed.
    """
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
    """Collect changed/untracked flags for paths under ``tree_root``.

    File-level flags are propagated upward to ancestor directories up to the
    requested ``tree_root`` so collapsed directories can still show status badges.
    """
    tree_root = tree_root.resolve()
    repo_root, _git_dir = _resolve_repo_and_git_dir(tree_root, timeout_seconds)
    if repo_root is None:
        return {}

    status_proc = _run_git(
        repo_root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        timeout_seconds,
    )
    if status_proc is None or status_proc.returncode != 0:
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
