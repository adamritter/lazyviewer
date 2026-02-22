"""Gitignore-aware path filtering utilities.

Builds a matcher by querying git for ignored files and directories.
Tree/index builders use this to optionally hide ignored content.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

def clear_gitignore_cache() -> None:
    # Kept for API compatibility with call sites/tests.
    return


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class GitIgnoreMatcher:
    root: Path
    ignored_files: frozenset[Path]
    ignored_dirs: tuple[Path, ...]

    def is_ignored(self, path: Path) -> bool:
        resolved = path.resolve()
        if not _is_within(resolved, self.root):
            return False
        if resolved in self.ignored_files:
            return True
        for ignored_dir in self.ignored_dirs:
            if resolved == ignored_dir or ignored_dir in resolved.parents:
                return True
        return False


def _load_matcher(root: Path) -> GitIgnoreMatcher | None:
    if shutil.which("git") is None:
        return None

    root = root.resolve()
    try:
        top_proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
    except Exception:
        return None

    top_level = top_proc.stdout.strip()
    if not top_level:
        return None

    repo_root = Path(top_level).resolve()
    if not _is_within(root, repo_root):
        return None

    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "-z",
                "--others",
                "-i",
                "--exclude-standard",
                "--directory",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except Exception:
        return None

    ignored_files: set[Path] = set()
    ignored_dirs: set[Path] = set()
    for raw in proc.stdout.split(b"\x00"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        is_dir = rel.endswith("/")
        rel = rel.rstrip("/")
        if not rel:
            continue
        abs_path = (repo_root / rel).resolve()
        if not _is_within(abs_path, root):
            continue
        if is_dir:
            ignored_dirs.add(abs_path)
            continue
        if abs_path.is_dir():
            ignored_dirs.add(abs_path)
        else:
            ignored_files.add(abs_path)

    return GitIgnoreMatcher(
        root=root,
        ignored_files=frozenset(ignored_files),
        ignored_dirs=tuple(sorted(ignored_dirs, key=lambda p: len(str(p)))),
    )


def get_gitignore_matcher(root: Path) -> GitIgnoreMatcher | None:
    return _load_matcher(root.resolve())
