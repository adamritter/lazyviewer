"""Jump, history, and named-mark operations for picker navigation."""

from __future__ import annotations

from pathlib import Path

from ....runtime.config import save_named_marks
from ....runtime.navigation import JumpLocation, is_named_mark_key


class NavigationHistoryMixin:
    """History and path/line jump operations used by picker interactions."""

    def current_jump_location(self) -> JumpLocation:
        """Capture current file path and scroll offsets as a jump location."""
        return JumpLocation(
            path=self.state.current_path.resolve(),
            start=max(0, self.state.start),
            text_x=max(0, self.state.text_x),
        )

    def record_jump_if_changed(self, origin: JumpLocation) -> None:
        """Record ``origin`` in jump history only when position actually changed."""
        normalized_origin = origin.normalized()
        if self.current_jump_location() == normalized_origin:
            return
        self.state.jump_history.record(normalized_origin)

    def apply_jump_location(self, location: JumpLocation) -> bool:
        """Apply jump location and clamp offsets to current rendered content."""
        target = location.normalized()
        current_path = self.state.current_path.resolve()
        path_changed = target.path != current_path
        if path_changed:
            self.jump_to_path(target.path)

        self.state.max_start = max(0, len(self.state.lines) - self.visible_content_rows())
        clamped_start = max(0, min(target.start, self.state.max_start))
        clamped_text_x = 0 if self.state.wrap_text else max(0, target.text_x)
        prev_start = self.state.start
        prev_text_x = self.state.text_x
        self.state.start = clamped_start
        self.state.text_x = clamped_text_x
        return path_changed or self.state.start != prev_start or self.state.text_x != prev_text_x

    def jump_back_in_history(self) -> bool:
        """Jump to previous history location, returning whether state changed."""
        target = self.state.jump_history.go_back(self.current_jump_location())
        if target is None:
            return False
        return self.apply_jump_location(target)

    def jump_forward_in_history(self) -> bool:
        """Jump to next history location, returning whether state changed."""
        target = self.state.jump_history.go_forward(self.current_jump_location())
        if target is None:
            return False
        return self.apply_jump_location(target)

    def set_named_mark(self, mark_key: str) -> bool:
        """Store current jump location under a valid named-mark key."""
        if not is_named_mark_key(mark_key):
            return False
        self.state.named_marks[mark_key] = self.current_jump_location()
        save_named_marks(self.state.named_marks)
        return True

    def jump_to_named_mark(self, mark_key: str) -> bool:
        """Jump to saved named mark and push current location onto history."""
        if not is_named_mark_key(mark_key):
            return False
        target = self.state.named_marks.get(mark_key)
        if target is None:
            return False
        origin = self.current_jump_location()
        if target.normalized() == origin:
            return False
        self.state.jump_history.record(origin)
        return self.apply_jump_location(target)

    def reveal_path_in_tree(self, target: Path) -> None:
        """Expand ancestor directories and rebuild tree focused on ``target``."""
        target = target.resolve()
        if target != self.state.tree_root:
            parent = target.parent
            while True:
                resolved = parent.resolve()
                if resolved == self.state.tree_root:
                    break
                self.state.expanded.add(resolved)
                if resolved.parent == resolved:
                    break
                parent = resolved.parent
        self.state.expanded.add(self.state.tree_root)
        self.rebuild_tree_entries(preferred_path=target, center_selection=True)
        self.mark_tree_watch_dirty()

    def jump_to_path(self, target: Path) -> None:
        """Reveal and open ``target`` path in tree and source preview state."""
        target = target.resolve()
        self.reveal_path_in_tree(target)
        self.state.current_path = target
        self.refresh_rendered_for_current_path()

    def jump_to_line(self, line_number: int) -> None:
        """Scroll source preview near ``line_number`` and reset horizontal offset."""
        visible_rows = max(1, self.visible_content_rows())
        self.state.max_start = max(0, len(self.state.lines) - visible_rows)
        max_line_index = max(0, len(self.state.lines) - 1)
        anchor = max(0, min(line_number, max_line_index))
        centered = max(0, anchor - max(1, visible_rows // 3))
        self.state.start = max(0, min(centered, self.state.max_start))
        self.state.text_x = 0
