from pathlib import Path
import gc
import tempfile
import unittest
from unittest import mock

from lazyviewer.render.ansi import ANSI_ESCAPE_RE, build_screen_lines
from lazyviewer.source_pane.lazy_syntax import LazyText, lazy_source
from lazyviewer.source_pane.paged_text import PagedText, PAGE_BYTES
from lazyviewer.source_pane.path import RenderedPath
from lazyviewer.file_tree_model.fs import list_directory_children
from lazyviewer.runtime.gc_control import defer_cyclic_gc
from lazyviewer.git_repository import resolve_repository


class PreviewBudgetTests(unittest.TestCase):
    def test_horizontal_view_does_not_tokenize_whole_minified_json_line(self):
        source = '{"values":[' + ','.join(str(i) for i in range(20000)) + ']}\n'
        text = lazy_source(source, Path('one-line.json'), 'monokai')
        lines = build_screen_lines(text, 79)
        lines.begin_frame(budget_seconds=1)
        rendered = lines.highlighted_line(0)
        self.assertEqual(ANSI_ESCAPE_RE.sub('', rendered), source)
        highlighter = text.render_line.__self__
        self.assertEqual(len(highlighter.highlighted), 0)
        self.assertLess(highlighter._partial_chars, 100)
        lines.begin_frame(budget_seconds=1, text_x=100)
        self.assertEqual(ANSI_ESCAPE_RE.sub('', lines.highlighted_line(0)), source)
        self.assertLess(highlighter._partial_chars, 200)
        # Moving left must not duplicate already-consumed characters.
        lines.begin_frame(budget_seconds=1, text_x=10)
        self.assertEqual(ANSI_ESCAPE_RE.sub('', lines.highlighted_line(0)), source)

    def test_expired_frame_keeps_readable_content_and_requests_another_frame(self):
        source = 'value = 12\n'*100
        text = lazy_source(source, Path('budget.py'), 'monokai')
        lines = build_screen_lines(text, 79)
        lines.begin_frame(budget_seconds=-1)
        self.assertEqual(lines.highlighted_line(0), 'value = 12\n')
        self.assertTrue(lines.needs_highlighting)
        lines.begin_frame(budget_seconds=1)
        self.assertIn('\x1b[', lines.highlighted_line(0))
        self.assertFalse(lines.needs_highlighting)

    def test_other_frame_work_does_not_starve_syntax_highlighting(self):
        clock = [0.0]
        source = 'value = 12\nnext_value = 34\n'
        text = lazy_source(source, Path('slow-tree.py'), 'monokai')
        lines = build_screen_lines(text, 79)
        with mock.patch('lazyviewer.source_pane.lazy_syntax.time.perf_counter',
                        side_effect=lambda: clock[0]):
            lines.begin_frame(budget_seconds=.0015)
            # Tree setup and row painting happen between source-pane calls.
            clock[0] += .020
            self.assertIn('\x1b[', lines.highlighted_line(0))
            clock[0] += .020
            self.assertIn('\x1b[', lines.highlighted_line(1))
        self.assertFalse(lines.needs_highlighting)

    def test_syntax_work_still_shares_one_budget_across_rows(self):
        clock = [0.0]
        calls = []
        def render_region(index, limit, deadline):
            calls.append(index)
            clock[0] += .002
            return '\x1b[34mfirst\x1b[0m\n'
        text = LazyText('first\nsecond\n', None, render_region)
        lines = build_screen_lines(text, 79)
        with mock.patch('lazyviewer.source_pane.lazy_syntax.time.perf_counter',
                        side_effect=lambda: clock[0]):
            lines.begin_frame(budget_seconds=.0015)
            self.assertIn('\x1b[', lines.highlighted_line(0))
            self.assertEqual(lines.highlighted_line(1), 'second\n')
        self.assertEqual(calls, [0])
        self.assertTrue(lines.needs_highlighting)

    def test_advancing_past_long_line_yields_at_deadline(self):
        source = 'values = [' + ','.join(str(i) for i in range(20000)) + ']\nnext = 2\n'
        text = lazy_source(source, Path('long.py'), 'monokai')
        highlighter = text.render_line.__self__
        self.assertIsNone(highlighter.region(1, 80, deadline=0))
        self.assertEqual(highlighter.highlighted, [])

    def test_large_interactive_file_reads_only_a_page_and_can_load_every_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'large.log'
            source='a log message\n'*(PAGE_BYTES//2)
            path.write_text(source)
            with mock.patch('lazyviewer.source_pane.path.os.isatty', return_value=True), \
                 mock.patch('lazyviewer.source_pane.path.read_text', side_effect=AssertionError('full read')):
                result=RenderedPath.from_path(path,False,'monokai',False,lazy_syntax=True)
            text=result.text
            self.assertIsInstance(text, PagedText)
            self.assertEqual(text.loaded_bytes, PAGE_BYTES)
            self.assertIn('scroll to load more', ''.join(text.screen_lines(79)))
            while text.grow():
                pass
            self.assertTrue(text.complete)
            self.assertEqual(''.join(text.screen_lines(79)),source)

    def test_large_file_can_seek_to_tail_and_back_without_loading_middle(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'large.log'
            source='HEAD\n'+'middle line\n'*100000+'TAIL\n'
            path.write_text(source)
            text=PagedText(path,path.stat().st_size)
            self.assertTrue(text.seek_end())
            tail=''.join(text.screen_lines(79))
            self.assertTrue(tail.endswith('TAIL\n'))
            self.assertNotIn('HEAD',tail)
            self.assertLessEqual(len(tail.encode()),PAGE_BYTES)
            self.assertIn('Bytes',text.progress_label)
            self.assertTrue(text.seek_start())
            self.assertTrue(''.join(text.screen_lines(79)).startswith('HEAD\n'))
            self.assertEqual(text.loaded_bytes,PAGE_BYTES)

    def test_utf8_character_split_at_page_boundary_is_not_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'unicode.log'
            source='a'*(PAGE_BYTES-1)+'é\n'+'b'*PAGE_BYTES
            path.write_text(source)
            text=PagedText(path,path.stat().st_size)
            while text.grow():
                pass
            self.assertEqual(''.join(text.screen_lines(79)),source)

    def test_page_growth_does_not_mix_changed_file_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'log.txt'
            path.write_text('old\n'*PAGE_BYTES)
            text=PagedText(path,path.stat().st_size)
            path.write_text('new\n'*PAGE_BYTES)
            text.grow()
            self.assertNotIn('old', ''.join(text.screen_lines(79)))
            self.assertIn('new', ''.join(text.screen_lines(79)))

    def test_directory_limit_keeps_sorted_order_and_metadata_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for i in range(100):
                (root/f'{i:03}.txt').write_text('test')
            (root/'z_directory').mkdir()
            (root/'.hidden').write_text('hidden')
            children,error,_=list_directory_children(root,False,limit=3)
            self.assertIsNone(error)
            self.assertEqual([c.name for c in children],['z_directory','000.txt','001.txt'])
            self.assertTrue(all(c.mtime_ns is not None for c in children))

    def test_gc_state_is_restored_on_exception_and_preserves_disabled_caller(self):
        original=gc.isenabled()
        try:
            gc.enable()
            with self.assertRaises(RuntimeError):
                with defer_cyclic_gc() as enabled:
                    self.assertTrue(enabled)
                    self.assertFalse(gc.isenabled())
                    raise RuntimeError('stop')
            self.assertTrue(gc.isenabled())
            gc.disable()
            with defer_cyclic_gc() as enabled:
                self.assertFalse(enabled)
            self.assertFalse(gc.isenabled())
        finally:
            if original: gc.enable()
            else: gc.disable()

    def test_new_nested_git_marker_is_not_hidden_by_negative_parent_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            child=root/'child';child.mkdir()
            self.assertEqual(resolve_repository(child,.2),(None,None))
            import subprocess
            subprocess.run(['git','init','-q',str(child)],check=True)
            self.assertEqual(resolve_repository(child,.2)[0],child.resolve())

    def test_erased_firmware_is_binary_without_a_nul_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'flash.bin'
            path.write_bytes(b'\xff'*4096)
            result=RenderedPath.from_path(path,False,'monokai',False,lazy_syntax=True)
            self.assertIn('<binary file:', result.text)

if __name__=='__main__':unittest.main()
