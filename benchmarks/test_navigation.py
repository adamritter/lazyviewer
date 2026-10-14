"""Tests for benchmark observations, independent of the optional emulator."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import navigation


def match(text, column, row=0):
    return SimpleNamespace(text=text, start=SimpleNamespace(column=column, row=row))


class ObservationTests(unittest.TestCase):
    def test_selection_and_preview_can_disagree(self):
        selected, preview = navigation.decode_matches([
            match('file_0002.py', 2), match('PREVIEW_0001', 42)], 120, 36)
        self.assertEqual((selected, preview), (2, 1))

    def test_ambiguous_repaint_is_not_reported_as_a_completed_preview(self):
        self.assertEqual(navigation.decode_matches([
            match('file_0002.py', 2), match('PREVIEW_0001', 42),
            match('PREVIEW_0002', 42, 1)], 120, 36), (2, None))

    def test_status_bar_and_wrong_pane_do_not_count(self):
        self.assertEqual(navigation.decode_matches([
            match('file_0009.py', 2, 35), match('PREVIEW_0009', 2),
            match('file_0008.py', 42)], 120, 36), (None, None))

    def test_repeated_preview_marker_is_one_file(self):
        self.assertEqual(navigation.decode_matches([
            match('file_0002.py', 2), match('PREVIEW_0002', 42),
            match('PREVIEW_0002', 42, 1)], 120, 36), (2, 2))

    def test_nearest_rank_percentiles_and_empty_samples(self):
        self.assertEqual(navigation.stats(range(1, 21))['p95_ms'], 19)
        self.assertEqual(navigation.stats([])['count'], 0)
        self.assertIsNone(navigation.stats([])['p95_ms'])


class PhaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_preview_after_selection_moves(self):
        events = []
        class Terminal:
            async def write(self, key):
                self.index = 1
                events.append('write')
            async def text(self):
                return 'final screen'
        terminal = Terminal()
        count = 0
        async def snapshot(locator, args):
            nonlocal count
            import time
            count += 1
            events.append('snapshot')
            return {'at_ns': time.monotonic_ns(), 'selection': 1,
                    'preview': 0 if count == 1 else 1, 'read_ms': 0.01}
        args = SimpleNamespace(timeout=1, poll_ms=.1, rate=30)
        with patch.object(navigation, 'screen_locator', return_value=object()), \
             patch.object(navigation, 'snapshot', side_effect=snapshot):
            result = await navigation.phase(terminal, args, 'test', 0, [1], False)
        self.assertEqual(events, ['write', 'snapshot', 'snapshot'])
        self.assertGreater(result['preview']['p95_ms'], result['selection']['p95_ms'])
        self.assertEqual(result['preview']['count'], 1)
        self.assertEqual(result['max_observed_pending_keys'], 0)

    async def test_incomplete_frame_is_not_counted_as_backlog_reset(self):
        class Terminal:
            async def write(self, key):
                pass
            async def text(self):
                return ''
        count = 0
        async def snapshot(locator, args):
            nonlocal count
            import time
            count += 1
            return {'at_ns': time.monotonic_ns(), 'selection': None if count == 1 else 10,
                    'preview': None if count == 1 else 10, 'read_ms': 0.01}
        args = SimpleNamespace(timeout=1, poll_ms=.1, rate=30)
        with patch.object(navigation, 'screen_locator', return_value=object()), \
             patch.object(navigation, 'snapshot', side_effect=snapshot):
            result = await navigation.phase(Terminal(), args, 'test', 9, [10], False)
        self.assertEqual(result['max_observed_pending_keys'], 0)

    async def test_stale_preview_fails_instead_of_claiming_success(self):
        class Terminal:
            async def write(self, key):
                pass
        async def snapshot(locator, args):
            import time
            return {'at_ns': time.monotonic_ns(), 'selection': 1,
                    'preview': 0, 'read_ms': 0.01}
        args = SimpleNamespace(timeout=.01, poll_ms=1, rate=30)
        with patch.object(navigation, 'screen_locator', return_value=object()), \
             patch.object(navigation, 'snapshot', side_effect=snapshot):
            with self.assertRaises(TimeoutError):
                await navigation.phase(Terminal(), args, 'test', 0, [1], False)


if __name__ == '__main__':
    unittest.main()
