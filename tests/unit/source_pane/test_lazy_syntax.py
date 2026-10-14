from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from pygments.token import Text

from lazyviewer.render.ansi import ANSI_ESCAPE_RE, build_screen_lines
from lazyviewer.source_pane import lazy_syntax
from lazyviewer.source_pane.lazy_syntax import LineHighlighter, LazyText, lazy_source
from lazyviewer.source_pane.path import RenderedPath
from lazyviewer.source_pane.source import extract_source_line_text, source_line_count, source_line_raw_text
from lazyviewer.source_pane.syntax import colorize_source
from lazyviewer.source_pane.diff import (
    DiffHunk, _build_annotated_source_preview, _build_lazy_annotated_source_preview,
    _colorize_lines, clear_diff_preview_cache,
)


class LazySyntaxTests(unittest.TestCase):
    def setUp(self):
        lazy_syntax.clear_syntax_cache()
        clear_diff_preview_cache()

    def tearDown(self):
        lazy_syntax.clear_syntax_cache()
        clear_diff_preview_cache()

    def test_startup_deferral_preserves_plain_text_and_retries_coloring(self):
        source = 'x = 1\ny = 2\nz = 3\n'
        highlighter = LineHighlighter(source, Path('deferred.py'), 'monokai')
        with mock.patch.object(lazy_syntax, '_WARMUP_FINISHED', False):
            with lazy_syntax.defer_syntax_initialization():
                self.assertIsNone(highlighter.region(2, 80))
                self.assertEqual(highlighter.highlighted, [])
                self.assertFalse(highlighter._failed)
            colored = highlighter.line(2)
        self.assertIn('\x1b[', colored)
        self.assertEqual(ANSI_ESCAPE_RE.sub('', colored), 'z = 3\n')

    def test_busy_preparation_does_not_block_or_poison_later_lines(self):
        import threading
        locked, release = threading.Event(), threading.Event()
        def prepare():
            with lazy_syntax._PREPARATION_LOCK:
                locked.set()
                release.wait(5)
        worker = threading.Thread(target=prepare)
        worker.start()
        self.assertTrue(locked.wait(5))
        highlighter = LineHighlighter('x = 1\ny = 2\n', Path('busy.py'), 'monokai')
        try:
            self.assertIsNone(highlighter.region(1, 80))
            self.assertFalse(highlighter._failed)
            self.assertEqual(highlighter.highlighted, [])
        finally:
            release.set()
            worker.join(5)
        self.assertIn('\x1b[', highlighter.line(1))

    def test_warmup_starts_only_one_daemon_worker(self):
        with mock.patch.object(lazy_syntax, '_WARMUP_STARTED', False), \
             mock.patch.object(lazy_syntax, '_WARMUP_FINISHED', False), \
             mock.patch.object(lazy_syntax.threading, 'Thread') as thread:
            lazy_syntax.start_interactive_syntax_warmup()
            lazy_syntax.start_interactive_syntax_warmup()
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs['daemon'])
        thread.return_value.start.assert_called_once()

    def test_layout_and_model_reads_do_not_start_the_lexer(self):
        source = '\n'.join(f'value_{n} = {n}' for n in range(5000))
        text = lazy_source(source, Path('/tmp/lazy.py'), 'monokai')
        highlighter = text.render_line.__self__
        with mock.patch.object(highlighter, '_start', side_effect=AssertionError('eager lexing')):
            rows = build_screen_lines(text, 80)
            self.assertEqual(source_line_count(rows, False), 5000)
            self.assertEqual(source_line_raw_text(rows, 3000, False), 'value_2999 = 2999')
            self.assertEqual(list(rows), source.splitlines(keepends=True))
        self.assertEqual(highlighter.highlighted, [])

    def test_only_requested_prefix_is_consumed_and_cached(self):
        source = 'hello\n' * 5000
        highlighter = LineHighlighter(source, Path('file.txt'), 'monokai')
        consumed = []
        class StreamingLexer:
            def get_tokens_unprocessed(self, text):
                for i, line in enumerate(text.splitlines(keepends=True)):
                    consumed.append(i)
                    yield i*len(line), Text, line
        lazy_syntax.syntax._ensure_pygments_loaded()
        with mock.patch.object(lazy_syntax, '_lexer_for_path',
                               return_value=StreamingLexer()):
            highlighter.line(34)
            self.assertEqual(consumed, list(range(35)))
            highlighter.line(3)
            highlighter.line(34)
            self.assertEqual(consumed, list(range(35)))
            highlighter.line(35)
            self.assertEqual(consumed, list(range(36)))

    def test_multiline_state_matches_eager_highlighting(self):
        cases = {
            'example.py': 'value = """hello\nclass is not a keyword here\nend"""\nnumber = 42\n',
            'example.c': '/* open\nint is a comment here\nend */\nint number = 42;\n',
            'example.js': '/* open\nfunction is a comment here\nend */\nconst number = 42;\n',
        }
        for filename, source in cases.items():
            with self.subTest(filename=filename):
                expected = colorize_source(source, Path(filename)).splitlines(keepends=True)
                highlighter = LineHighlighter(source, Path(filename), 'monokai')
                # Jump to a line inside a multiline token, then back and beyond it.
                for index in (1, 0, 3, 2):
                    self.assertEqual(highlighter.line(index), expected[index])

    def test_multiline_token_is_not_formatted_past_requested_line(self):
        source = 'value = """start\n' + 'still a string\n'*5000 + 'end"""\n'
        highlighter = LineHighlighter(source, Path('file.py'), 'monokai')
        highlighter.line(2)
        self.assertEqual(len(highlighter.highlighted), 3)
        self.assertIn('\x1b[33m', highlighter.line(1))

    def test_empty_leading_lines_missing_newline_and_tabs_are_preserved(self):
        for source in ('', '\n\nvalue = 1', '\tvalue = 2\n', '\n'):
            with self.subTest(source=source):
                highlighter = LineHighlighter(source, Path('file.py'), 'monokai')
                self.assertEqual(''.join(ANSI_ESCAPE_RE.sub('', highlighter.line(i))
                                        for i in range(len(highlighter.lines))), source)

    def test_wrapping_resize_and_sticky_source_share_lexer_progress(self):
        source = 'value = "a rather long string that wraps across rows"\n' + 'next = 3\n'*100
        text = lazy_source(source, Path('file.py'), 'monokai')
        rows = build_screen_lines(text, 12, wrap=True)
        for index in range(3):
            self.assertEqual(ANSI_ESCAPE_RE.sub('', rows.highlighted_line(index)), rows[index])
        self.assertEqual(len(text.render_line.__self__.highlighted), 0)
        self.assertIn('\x1b[', extract_source_line_text(rows, 1, 20, True, 0))
        resized = build_screen_lines(text, 20, wrap=True)
        self.assertEqual(ANSI_ESCAPE_RE.sub('', resized.highlighted_line(0)), resized[0])
        self.assertEqual(len(text.render_line.__self__.highlighted), 1)

    def test_document_cache_reuses_progress_and_detects_same_length_edits(self):
        path = Path('file.py')
        first = lazy_source('x = 1\ny = 2\n', path, 'monokai')
        first.render_line(0)
        again = lazy_source(str(first), path, 'monokai')
        self.assertIs(first, again)
        changed = lazy_source('x = 9\ny = 2\n', path, 'monokai')
        self.assertIsNot(first, changed)
        self.assertIn('9', ANSI_ESCAPE_RE.sub('', changed.render_line(0)))
        self.assertIsNot(changed, lazy_source(str(changed), path, 'default'))

    def test_cache_is_bounded_by_count_and_characters(self):
        with mock.patch.object(lazy_syntax, 'SYNTAX_CACHE_ENTRIES', 2), \
             mock.patch.object(lazy_syntax, 'SYNTAX_CACHE_CHARACTERS', 20):
            one = lazy_source('one\n', Path('one.py'), 'monokai')
            lazy_source('two\n', Path('two.py'), 'monokai')
            lazy_source('three\n', Path('three.py'), 'monokai')
            self.assertEqual(len(lazy_syntax._CACHE), 2)
            self.assertIsNot(one, lazy_source('one\n', Path('one.py'), 'monokai'))
            lazy_source('long = 123456789\n', Path('long.py'), 'monokai')
            self.assertLessEqual(lazy_syntax._cache_characters, 20)

    def test_broken_lexer_falls_back_without_losing_source(self):
        text = lazy_source('one\ntwo\n', Path('file.py'), 'monokai')
        with mock.patch.object(text.render_line.__self__, '_start', side_effect=RuntimeError):
            self.assertEqual(text.render_line(1), 'two\n')
            self.assertEqual(text.render_line(0), 'one\n')

    def test_interactive_path_is_lazy_and_edits_invalidate_it(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'file.py'
            path.write_text('value = 1\n'*2000)
            with mock.patch('lazyviewer.source_pane.path.os.isatty', return_value=True):
                result = RenderedPath.from_path(path, False, 'monokai', False,
                                                prefer_git_diff=False, lazy_syntax=True)
                self.assertIsInstance(result.text, LazyText)
                self.assertEqual(result.text.render_line.__self__.highlighted, [])
                build_screen_lines(result.text, 80).highlighted_line(0)
                path.write_text('value = 9\n'*2000)
                changed = RenderedPath.from_path(path, False, 'monokai', False,
                                                 prefer_git_diff=False, lazy_syntax=True)
                self.assertIsNot(result.text, changed.text)
                self.assertIn('9', ANSI_ESCAPE_RE.sub('', changed.text.render_line(0)))

    def test_no_color_remains_plain_text(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'file.py'
            path.write_text('value = 1\n')
            result = RenderedPath.from_path(path, False, 'monokai', True,
                                            prefer_git_diff=False, lazy_syntax=True)
            self.assertEqual(type(result.text), str)
            self.assertEqual(result.text, 'value = 1\n')

    def test_diff_rows_match_eager_colors_and_source_mapping(self):
        source = 'value = """open\nstill string\nend"""\nnext = 3\n'
        path = Path('file.py')
        hunks = [DiffHunk(2, 1, 2, 1, ['old string'])]
        eager = _build_annotated_source_preview(source.splitlines(),
            _colorize_lines(source.splitlines(), path, 'monokai', True),
            hunks, path, 'monokai', True)
        text = _build_lazy_annotated_source_preview(source, hunks, path, 'monokai')
        document = lazy_source(source, path, 'monokai')
        rows = build_screen_lines(text, 80)
        self.assertEqual(document.render_line.__self__.highlighted, [])
        expected = eager.splitlines()
        for i in (2, 0, 4, 1, 3):
            self.assertEqual(rows.highlighted_line(i).rstrip('\n'), expected[i])
        self.assertEqual(source_line_count(rows, False, preview_is_git_diff=True), 4)

    def test_diff_end_deletion_and_empty_source(self):
        for source, hunk in [('x = 1\n', DiffHunk(2, 1, 2, 0, ['deleted'])),
                              ('', DiffHunk(1, 1, 0, 0, ['deleted']))]:
            with self.subTest(source=source):
                text = _build_lazy_annotated_source_preview(source, [hunk], Path('f.py'), 'monokai')
                rows = build_screen_lines(text, 80)
                self.assertIn('deleted', ANSI_ESCAPE_RE.sub('', rows.highlighted_line(len(rows)-1)))


if __name__ == '__main__':
    unittest.main()
