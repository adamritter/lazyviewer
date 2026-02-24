"""Tests for jump-history semantics and mark-key validation.

Confirms deduping, back/forward behavior, and max-history limits.
Also checks which single-character keys are valid named marks.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from lazyviewer.runtime.navigation import JumpHistory, JumpLocation, is_named_mark_key


def _loc(name: str, start: int = 0, text_x: int = 0) -> JumpLocation:
    return JumpLocation(Path(f"/tmp/{name}"), start=start, text_x=text_x).normalized()


class JumpHistoryTests(unittest.TestCase):
    def test_back_and_forward_round_trip(self) -> None:
        history = JumpHistory()
        source = _loc("source.py", start=4, text_x=2)
        current = _loc("target.py", start=10, text_x=1)

        history.record(source)
        back_target = history.go_back(current)
        self.assertEqual(back_target, source)

        forward_target = history.go_forward(source)
        self.assertEqual(forward_target, current)

    def test_record_clears_forward_stack(self) -> None:
        history = JumpHistory()
        one = _loc("one.py")
        two = _loc("two.py")
        three = _loc("three.py")

        history.record(one)
        history.go_back(two)
        self.assertEqual(len(history.forward), 1)

        history.record(three)
        self.assertEqual(history.forward, [])

    def test_record_avoids_adjacent_duplicates(self) -> None:
        history = JumpHistory()
        place = _loc("same.py", start=3, text_x=1)

        history.record(place)
        history.record(place)

        self.assertEqual(history.back, [place])

    def test_history_respects_max_entries(self) -> None:
        history = JumpHistory(max_entries=2)
        first = _loc("first.py")
        second = _loc("second.py")
        third = _loc("third.py")

        history.record(first)
        history.record(second)
        history.record(third)

        self.assertEqual(history.back, [second, third])


class NamedMarkKeyTests(unittest.TestCase):
    def test_named_mark_key_validation(self) -> None:
        self.assertTrue(is_named_mark_key("a"))
        self.assertTrue(is_named_mark_key("7"))
        self.assertTrue(is_named_mark_key("'"))
        self.assertFalse(is_named_mark_key(""))
        self.assertFalse(is_named_mark_key("ab"))
        self.assertFalse(is_named_mark_key(" "))


if __name__ == "__main__":
    unittest.main()
