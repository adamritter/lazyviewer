"""Filesystem and git-change watch signatures.

Computes cheap hashes over relevant tree/git metadata for poll-based refreshes.
Runtime code compares these signatures to detect when to rebuild view state.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


def _update_digest(digest, token: str) -> None:
    """Append a token plus separator byte to a hash digest."""
    digest.update(token.encode("utf-8", errors="surrogateescape"))
    digest.update(b"\0")


def _path_stat_signature(path: Path) -> tuple[str, int, int, int]:
    """Return a stable stat tuple describing ``path`` existence and metadata."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return ("missing", 0, 0, 0)
    except OSError:
        return ("error", 0, 0, 0)
    return ("ok", st.st_mtime_ns, st.st_size, st.st_mode)


def resolve_git_paths(tree_root: Path, timeout_seconds: float = 0.15) -> tuple[Path | None, Path | None]:
    """Resolve repository root and git-dir for ``tree_root``.

    Uses ``git rev-parse --show-toplevel --git-dir`` and returns ``(None, None)``
    if git is unavailable, the directory is not in a repo, or probing fails.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(tree_root), "rev-parse", "--show-toplevel", "--git-dir"],
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


def build_tree_watch_signature(root: Path, expanded: set[Path], show_hidden: bool) -> str:
    """Build a digest for visible tree structure under expanded directories.

    The signature includes root path, hidden-file mode, each watched directory's
    own stat state, and sorted child metadata for directories currently expanded.
    """
    root = root.resolve()
    watched_dirs: set[Path] = {root}
    for path in expanded:
        try:
            resolved = path.resolve()
        except Exception:
            continue
        if not resolved.is_relative_to(root):
            continue
        watched_dirs.add(resolved)

    digest = hashlib.blake2b(digest_size=20)
    _update_digest(digest, f"root:{root}")
    _update_digest(digest, f"show_hidden:{1 if show_hidden else 0}")

    for directory in sorted(watched_dirs, key=lambda p: str(p)):
        _update_digest(digest, f"dir:{directory}")
        stat_state, _stat_mtime, _stat_size, stat_mode = _path_stat_signature(directory)
        _update_digest(digest, f"dir_stat:{stat_state}:{stat_mode}")
        if stat_state != "ok":
            continue
        if not directory.is_dir():
            _update_digest(digest, "children:not_dir")
            continue

        children: list[tuple[str, bool, int, int, int, str]] = []
        try:
            with os.scandir(directory) as entries:
                for child in entries:
                    name = child.name
                    if not show_hidden and name.startswith("."):
                        continue
                    try:
                        is_dir = child.is_dir(follow_symlinks=False)
                    except OSError:
                        is_dir = False
                    try:
                        st = child.stat(follow_symlinks=False)
                        mtime_ns = st.st_mtime_ns
                        size = st.st_size
                        mode = st.st_mode
                        state = "ok"
                    except OSError:
                        mtime_ns = 0
                        size = 0
                        mode = 0
                        state = "error"
                    children.append((name, is_dir, mtime_ns, size, mode, state))
        except OSError:
            _update_digest(digest, "children:error")
            continue

        children.sort(key=lambda item: (not item[1], item[0].casefold(), item[0]))
        for name, is_dir, mtime_ns, size, mode, state in children:
            _update_digest(
                digest,
                f"child:{name}:{1 if is_dir else 0}:{state}:{mtime_ns}:{size}:{mode}",
            )

    return digest.hexdigest()


def build_git_watch_signature(git_dir: Path | None) -> str:
    """Build a digest over git metadata that signals status-relevant changes."""
    digest = hashlib.blake2b(digest_size=20)
    if git_dir is None:
        _update_digest(digest, "git:none")
        return digest.hexdigest()

    git_dir = git_dir.resolve()
    _update_digest(digest, f"git_dir:{git_dir}")

    def add_path_token(label: str, path: Path) -> None:
        """Add stat metadata for one git control file."""
        state, mtime_ns, size, mode = _path_stat_signature(path)
        _update_digest(digest, f"{label}:{state}:{mtime_ns}:{size}:{mode}")

    add_path_token("index", git_dir / "index")
    head_path = git_dir / "HEAD"
    add_path_token("head", head_path)

    ref_name = ""
    try:
        head_text = head_path.read_text(encoding="utf-8", errors="replace").strip()
        if head_text.startswith("ref: "):
            ref_name = head_text[5:].strip()
    except Exception:
        ref_name = ""
    _update_digest(digest, f"head_ref:{ref_name}")

    if ref_name:
        add_path_token("head_ref_file", git_dir / ref_name)

    add_path_token("merge_head", git_dir / "MERGE_HEAD")
    add_path_token("cherry_pick_head", git_dir / "CHERRY_PICK_HEAD")
    add_path_token("rebase_head", git_dir / "REBASE_HEAD")

    return digest.hexdigest()
