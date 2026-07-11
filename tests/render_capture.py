"""I/O adapter used only by renderer tests."""

from __future__ import annotations

import os

from lazyviewer.render import Frame, render_dual_page as build_frame
from lazyviewer.render.help import render_help_page as build_help_frame


def render_dual_page(*args, **kwargs) -> Frame:
    frame = build_frame(*args, **kwargs)
    os.write(1, frame.data)
    return frame


def render_help_page(*args, **kwargs) -> Frame:
    frame = build_help_frame(*args, **kwargs)
    os.write(1, frame.data)
    return frame
