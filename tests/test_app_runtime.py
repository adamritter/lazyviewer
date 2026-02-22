from __future__ import annotations

import unittest

from lazyviewer.app_runtime import _centered_scroll_start, _first_git_change_screen_line


class AppRuntimeBehaviorTests(unittest.TestCase):
    def test_first_git_change_screen_line_handles_plain_and_ansi_markers(self) -> None:
        plain_lines = [
            "  unchanged",
            "- removed",
            "+ added",
        ]
        self.assertEqual(_first_git_change_screen_line(plain_lines), 1)

        ansi_lines = [
            "\033[2;38;5;245m  \033[0munchanged",
            "\033[38;5;42m+ \033[0madded",
        ]
        self.assertEqual(_first_git_change_screen_line(ansi_lines), 1)

        background_lines = [
            "\033[38;5;252munchanged\033[0m",
            "\033[38;5;252;48;5;22madded\033[0m",
        ]
        self.assertEqual(_first_git_change_screen_line(background_lines), 1)

        truecolor_background_lines = [
            "\033[38;5;252munchanged\033[0m",
            "\033[38;2;220;220;220;48;2;36;74;52madded\033[0m",
        ]
        self.assertEqual(_first_git_change_screen_line(truecolor_background_lines), 1)

    def test_first_git_change_screen_line_returns_none_without_markers(self) -> None:
        self.assertIsNone(_first_git_change_screen_line(["x = 1", "y = 2"]))

    def test_centered_scroll_start_clamps_and_interpolates(self) -> None:
        self.assertEqual(_centered_scroll_start(target_line=30, max_start=40, visible_rows=12), 26)
        self.assertEqual(_centered_scroll_start(target_line=1, max_start=40, visible_rows=12), 0)
        self.assertEqual(_centered_scroll_start(target_line=120, max_start=40, visible_rows=12), 36)


if __name__ == "__main__":
    unittest.main()
