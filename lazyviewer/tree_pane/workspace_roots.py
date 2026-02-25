"""Workspace-root normalization and layout helpers for tree-pane UI."""

from __future__ import annotations

from pathlib import Path


def normalized_workspace_roots(tree_roots: list[Path], active_root: Path) -> list[Path]:
    """Return resolved roots preserving order/duplicates; append active if missing."""
    normalized = [raw_root.resolve() for raw_root in tree_roots]
    resolved_active = active_root.resolve()
    if not any(root == resolved_active for root in normalized):
        normalized.append(resolved_active)
    return normalized


def normalized_workspace_expanded_sections(
    tree_roots: list[Path],
    active_root: Path,
    workspace_expanded: list[set[Path]],
    expanded_fallback: set[Path],
    *,
    include_active: bool = True,
) -> tuple[list[Path], list[set[Path]], set[Path]]:
    """Normalize per-section expanded state aligned to workspace-root positions."""
    if include_active:
        roots = normalized_workspace_roots(tree_roots, active_root)
    else:
        roots = [raw_root.resolve() for raw_root in tree_roots]
    sections: list[set[Path]] = []
    union: set[Path] = set()
    for idx, root in enumerate(roots):
        source = workspace_expanded[idx] if idx < len(workspace_expanded) else expanded_fallback
        normalized = {
            candidate.resolve()
            for candidate in source
            if candidate.resolve().is_relative_to(root)
        }
        sections.append(normalized)
        union.update(normalized)
    return roots, sections, union


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
