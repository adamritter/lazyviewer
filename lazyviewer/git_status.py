"""Git status overlay helpers plus compatibility exports for preview diff code.

Collects changed/untracked flags for tree badges and directory ancestors.
Preview-diff rendering now lives under ``lazyviewer.preview.diff``.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

GIT_STATUS_CHANGED = 1
GIT_STATUS_UNTRACKED = 2
_ADDED_BG_SGR = "48;2;36;74;52"
_REMOVED_BG_SGR = "48;2;92;43;49"


def clear_diff_preview_cache() -> None:
    from .preview.diff import clear_diff_preview_cache as _clear_diff_preview_cache

    _clear_diff_preview_cache()


def build_unified_diff_preview_for_path(
    target: Path,
    timeout_seconds: float = 0.2,
    colorize: bool = True,
    style: str = "monokai",
) -> str | None:
    from .preview.diff import build_unified_diff_preview_for_path as _build_diff_preview

    return _build_diff_preview(
        target,
        timeout_seconds=timeout_seconds,
        colorize=colorize,
        style=style,
    )


def _boost_foreground_contrast_for_diff(params: str) -> str:
    from .preview.diff import _boost_foreground_contrast_for_diff as _boost_contrast

    return _boost_contrast(params)


def _apply_line_background(code_line: str, bg_sgr: str) -> str:
    from .preview.diff import _apply_line_background as _apply_bg

    return _apply_bg(code_line, bg_sgr)


def _merge_flags(overlay: dict[Path, int], target: Path, flags: int) -> None:
    overlay[target] = overlay.get(target, 0) | flags


def format_git_status_badges(path: Path, git_status_overlay: dict[Path, int] | None) -> str:
    if not git_status_overlay:
        return ""

    flags = git_status_overlay.get(path.resolve(), 0)
    if flags == 0:
        return ""

    badges: list[str] = []
    if flags & GIT_STATUS_CHANGED:
        badges.append("\033[38;5;214m[M]\033[0m")
    if flags & GIT_STATUS_UNTRACKED:
        badges.append("\033[38;5;42m[?]\033[0m")
    if not badges:
        return ""
    return " " + "".join(badges)


def _resolve_repo_and_git_dir(path: Path, timeout_seconds: float) -> tuple[Path | None, Path | None]:
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
    repo_root, _git_dir = _resolve_repo_and_git_dir(tree_root, timeout_seconds)
    if repo_root is None:
        return {}

    status_proc = _run_git(
        repo_root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=normal"],
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
