"""Module entrypoint for ``python -m lazyviewer``.

This keeps module-mode execution behavior identical to the CLI script.
All argument parsing and runtime setup happen in ``lazyviewer.cli``.
"""

from .cli import main


if __name__ == "__main__":
    main()
