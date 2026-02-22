"""Regression tests for ANSI line-building primitives.

Focuses on wrapped vs. unwrapped ``build_screen_lines`` behavior.
These cases protect status and viewport math from line-shaping regressions.
"""

import unittest

from lazyviewer import ansi as ansi_mod


class BuildScreenLinesTests(unittest.TestCase):
    def test_unwrapped_mode_preserves_original_lines(self) -> None:
        rendered = "abcdef\nxy\n"
        lines = ansi_mod.build_screen_lines(rendered, width=3, wrap=False)
        self.assertEqual(lines, ["abcdef\n", "xy\n"])

    def test_wrapped_mode_splits_lines_to_width(self) -> None:
        rendered = "abcdef\nxy\n"
        lines = ansi_mod.build_screen_lines(rendered, width=3, wrap=True)
        self.assertEqual(lines, ["abc", "def\n", "xy\n"])

    def test_wrapped_mode_handles_empty_input(self) -> None:
        lines = ansi_mod.build_screen_lines("", width=5, wrap=True)
        self.assertEqual(lines, [""])


if __name__ == "__main__":
    unittest.main()
