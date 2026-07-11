"""Pure logical-frame differ and ANSI patch encoder."""

from __future__ import annotations

from dataclasses import dataclass

from .ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line
from .frame import Frame, Surface


@dataclass(frozen=True, slots=True)
class FramePatch:
    """One atomic terminal update derived from two logical frames."""

    data: bytes
    rows_touched: int
    surfaces_touched: int
    full_repaint: bool


def same_layout(previous: Frame, current: Frame) -> bool:
    """Return whether frames can be safely updated surface-by-surface."""
    if not previous.is_structured or not current.is_structured:
        return False
    if previous.width != current.width or previous.height != current.height:
        return False
    previous_layout = tuple((surface.name, surface.rect) for surface in previous.surfaces)
    current_layout = tuple((surface.name, surface.rect) for surface in current.surfaces)
    return previous_layout == current_layout


def encode_surface_row(
    surface: Surface,
    row_index: int,
    row: str,
    *,
    screen_width: int | None = None,
) -> str:
    """Encode one self-contained exact-width surface-row repaint."""
    width = surface.rect.width
    clipped = clip_ansi_line(row, width)
    plain = ANSI_ESCAPE_RE.sub("", clipped)
    display_width = 0
    for char in plain:
        display_width += char_display_width(char, display_width)
    padding = " " * max(0, width - display_width)
    reaches_screen_edge = (
        screen_width is not None and surface.rect.x + surface.rect.width == screen_width
    )
    erase_suffix = "\x1b[K" if reaches_screen_edge and padding else padding
    terminal_row = surface.rect.y + row_index + 1
    terminal_column = surface.rect.x + 1
    return (
        f"\x1b[{terminal_row};{terminal_column}H"
        f"\x1b[0m{clipped}\x1b[0m{erase_suffix}"
    )


def encode_structured_frame(frame: Frame) -> bytes:
    """Encode a recovery repaint directly from retained frame surfaces."""
    if not frame.is_structured:
        return frame.data
    output = ["\x1b[H\x1b[J"]
    for surface in frame.surfaces:
        for row_index, row in enumerate(surface.rows):
            output.append(
                encode_surface_row(
                    surface,
                    row_index,
                    row,
                    screen_width=frame.width,
                )
            )
    return "".join(output).encode("utf-8", errors="replace")


def diff_frames(previous: Frame | None, current: Frame) -> FramePatch:
    """Build a full repaint or minimal changed-row patch for ``current``."""
    if previous is None or not current.is_structured or not same_layout(previous, current):
        canonical_data = current.data
        data = canonical_data or encode_structured_frame(current)
        return FramePatch(
            data=data,
            rows_touched=(
                current.height
                if canonical_data or not current.is_structured
                else sum(surface.rect.height for surface in current.surfaces)
            ),
            surfaces_touched=len(current.surfaces),
            full_repaint=True,
        )

    previous_by_name = {surface.name: surface for surface in previous.surfaces}
    patch: list[str] = []
    rows_touched = 0
    surfaces_touched = 0
    for surface in current.surfaces:
        old_surface = previous_by_name[surface.name]
        changed_surface = False
        for row_index, row in enumerate(surface.rows):
            if row == old_surface.rows[row_index]:
                continue
            patch.append(
                encode_surface_row(
                    surface,
                    row_index,
                    row,
                    screen_width=current.width,
                )
            )
            rows_touched += 1
            changed_surface = True
        if changed_surface:
            surfaces_touched += 1

    return FramePatch(
        data="".join(patch).encode("utf-8", errors="replace"),
        rows_touched=rows_touched,
        surfaces_touched=surfaces_touched,
        full_repaint=False,
    )


__all__ = [
    "FramePatch",
    "diff_frames",
    "encode_structured_frame",
    "encode_surface_row",
    "same_layout",
]
