"""Tree-pane width helpers."""

from __future__ import annotations


def compute_left_width(total_width: int) -> int:
    """Choose default tree-pane width from total terminal width."""
    if total_width <= 60:
        return max(16, total_width // 2)
    return max(20, min(40, total_width // 3))


def clamp_left_width(total_width: int, desired_left: int) -> int:
    """Clamp requested tree-pane width to safe viewport bounds."""
    max_possible = max(1, total_width - 2)
    min_left = max(12, min(20, total_width - 12))
    max_left = max(min_left, total_width - 12)
    max_left = min(max_left, max_possible)
    min_left = min(min_left, max_left)
    return max(min_left, min(desired_left, max_left))
