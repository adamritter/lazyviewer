"""CLI argument and default-path behavior tests.

Verifies how ``lazyviewer.cli.main`` chooses target paths and source text.
Prevents regressions in command-line entrypoint ergonomics.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer import cli


class CliDefaultPathTests(unittest.TestCase):
    def test_main_defaults_to_current_working_directory_when_no_path_arg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(sys, "argv", ["lv"]), mock.patch("lazyviewer.cli.run_pager") as run_pager:
                    cli.main()
            finally:
                os.chdir(previous_cwd)

            run_pager.assert_called_once()
            source, path, style, no_color, nopager = run_pager.call_args.args
            self.assertEqual(source, "")
            self.assertEqual(path.resolve(), root)
            self.assertEqual(style, "monokai")
            self.assertFalse(no_color)
            self.assertFalse(nopager)

    def test_main_uses_explicit_path_argument_over_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            target = root / "target.txt"
            target.write_text("hello\n", encoding="utf-8")

            with mock.patch.object(sys, "argv", ["lv", str(target)]), mock.patch("lazyviewer.cli.run_pager") as run_pager:
                cli.main(default_path=root / "unused.txt")

            run_pager.assert_called_once()
            source, path, *_rest = run_pager.call_args.args
            self.assertEqual(source, "hello\n")
            self.assertEqual(path.resolve(), target.resolve())


if __name__ == "__main__":
    unittest.main()
