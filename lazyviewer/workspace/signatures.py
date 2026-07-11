"""Filesystem and Git revision signatures for workspace observations."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def _token(digest, value: str) -> None:
    digest.update(value.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")


def _stat(path: Path) -> tuple[str, int, int, int]:
    try:
        value = path.stat()
    except FileNotFoundError:
        return "missing", 0, 0, 0
    except OSError:
        return "error", 0, 0, 0
    return "ok", int(value.st_mtime_ns), int(value.st_size), int(value.st_mode)


def resolve_git_paths(root: Path, timeout_seconds: float = 0.15) -> tuple[Path | None, Path | None]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel", "--git-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if completed.returncode != 0 or len(lines) < 2:
        return None, None
    repo_root = Path(lines[0]).resolve()
    raw_git_dir = Path(lines[1])
    git_dir = raw_git_dir if raw_git_dir.is_absolute() else repo_root / raw_git_dir
    return repo_root, git_dir.resolve()


def build_tree_watch_signature(root: Path, expanded: set[Path], show_hidden: bool) -> str:
    """Hash visible children beneath every expanded directory for one root."""
    root = root.resolve()
    watched = {root}
    for candidate in expanded:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_relative_to(root):
            watched.add(resolved)

    digest = hashlib.blake2b(digest_size=20)
    _token(digest, f"root:{root}")
    _token(digest, f"hidden:{int(show_hidden)}")
    for directory in sorted(watched, key=str):
        _token(digest, f"directory:{directory}")
        state, _mtime, _size, mode = _stat(directory)
        _token(digest, f"directory-stat:{state}:{mode}")
        if state != "ok" or not directory.is_dir():
            continue
        observed: list[tuple[str, bool, int, int, int, str]] = []
        try:
            with os.scandir(directory) as entries:
                for child in entries:
                    if not show_hidden and child.name.startswith("."):
                        continue
                    try:
                        is_dir = child.is_dir(follow_symlinks=False)
                    except OSError:
                        is_dir = False
                    try:
                        stat = child.stat(follow_symlinks=False)
                        observed.append(
                            (
                                child.name,
                                is_dir,
                                int(stat.st_mtime_ns),
                                int(stat.st_size),
                                int(stat.st_mode),
                                "ok",
                            )
                        )
                    except OSError:
                        observed.append((child.name, is_dir, 0, 0, 0, "error"))
        except OSError:
            _token(digest, "children:error")
            continue
        for item in sorted(observed, key=lambda value: (not value[1], value[0].casefold(), value[0])):
            _token(digest, f"child:{item!r}")
    return digest.hexdigest()


def build_git_watch_signature(git_dir: Path | None) -> str:
    digest = hashlib.blake2b(digest_size=20)
    if git_dir is None:
        _token(digest, "git:none")
        return digest.hexdigest()
    git_dir = git_dir.resolve()
    _token(digest, f"git-dir:{git_dir}")

    def add(label: str, path: Path) -> None:
        _token(digest, f"{label}:{_stat(path)!r}")

    add("index", git_dir / "index")
    head = git_dir / "HEAD"
    add("head", head)
    ref_name = ""
    try:
        head_text = head.read_text(encoding="utf-8", errors="replace").strip()
        if head_text.startswith("ref: "):
            ref_name = head_text[5:].strip()
    except OSError:
        pass
    _token(digest, f"head-ref:{ref_name}")
    if ref_name:
        add("head-ref-file", git_dir / ref_name)
    add("merge-head", git_dir / "MERGE_HEAD")
    add("cherry-pick-head", git_dir / "CHERRY_PICK_HEAD")
    add("rebase-head", git_dir / "REBASE_HEAD")
    return digest.hexdigest()
