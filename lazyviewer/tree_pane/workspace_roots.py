"""Workspace-root normalization and layout helpers for tree-pane UI."""

from __future__ import annotations

from pathlib import Path


def normalized_workspace_roots(tree_roots: list[Path], active_root: Path) -> list[Path]:
    """Return deduped workspace roots, ensuring active root is included."""
    normalized: list[Path] = []
    seen: set[Path] = set()
    for raw_root in tree_roots:
        resolved = raw_root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(resolved)

    resolved_active = active_root.resolve()
    if resolved_active not in seen:
        normalized.append(resolved_active)
    return normalized


def workspace_root_display_labels(tree_roots: list[Path], active_root: Path) -> list[str]:
    """Return compact labels that keep multiple roots visually distinguishable."""
    roots = normalized_workspace_roots(tree_roots, active_root)
    if not roots:
        return []

    parts_lists = [root.parts for root in roots]
    common_len = min(len(parts) for parts in parts_lists)
    for idx in range(common_len):
        token = parts_lists[0][idx]
        if any(parts[idx] != token for parts in parts_lists[1:]):
            common_len = idx
            break

    # Keep one shared segment for context (for example ``repo/subdir`` instead
    # of just ``subdir``) while still dropping long absolute prefixes.
    start_idx = max(1, common_len - 1)

    labels: list[str] = []
    for root in roots:
        suffix = root.parts[start_idx:]
        if suffix:
            labels.append("/".join(suffix))
        else:
            labels.append(root.name or str(root))
    return labels


def workspace_root_banner_rows(tree_roots: list[Path], active_root: Path, picker_active: bool) -> int:
    """Return top-of-pane banner rows (disabled with forest-style multiroot tree)."""
    _ = tree_roots
    _ = active_root
    _ = picker_active
    return 0
