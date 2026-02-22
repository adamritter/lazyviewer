"""Editor launch helper for external file or directory edits.

Runs ``$EDITOR`` while temporarily leaving raw/alternate-screen TUI mode.
Returns an error message string instead of raising for UI-friendly handling.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Callable


def launch_editor(
    target: Path,
    disable_tui_mode: Callable[[], None],
    enable_tui_mode: Callable[[], None],
) -> str | None:
    editor_env = os.environ.get("EDITOR", "").strip()
    if not editor_env:
        return "Cannot edit: $EDITOR is not set."
    cmd = shlex.split(editor_env)
    if not cmd:
        return "Cannot edit: $EDITOR is empty."

    disable_tui_mode()
    try:
        subprocess.run([*cmd, str(target)], check=False)
    except Exception as exc:
        return f"Failed to launch editor: {exc}"
    finally:
        enable_tui_mode()
    return None
