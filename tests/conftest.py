"""Pytest bootstrap for local source imports.

The ``pytest`` console script can run with a sys.path that excludes the
repository root. Ensure ``import lazyviewer`` resolves to the local package.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT_STR = str(PROJECT_ROOT)

if PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, PROJECT_ROOT_STR)
