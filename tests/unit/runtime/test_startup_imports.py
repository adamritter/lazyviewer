"""Fresh-process checks for optional imports and GC restoration."""
import os
from pathlib import Path
import subprocess
import sys


def run_python(source):
    root = Path(__file__).resolve().parents[3]
    return subprocess.run([sys.executable, '-c', source], cwd=root,
                          env=dict(os.environ, PYTHONPATH=str(root)),
                          capture_output=True, text=True, check=True)


def test_bootstrap_keeps_optional_features_unloaded():
    run_python("""
import sys
import lazyviewer.cli
import lazyviewer.runtime.app
for name in ('concurrent.futures', 'platformdirs',
             'lazyviewer.source_pane.symbols',
             'lazyviewer.source_pane.symbols_config',
             'lazyviewer.runtime.loop_callbacks',
             'lazyviewer.input.key_context',
             'lazyviewer.runtime.directory_prefetch',
             'lazyviewer.runtime.git_jumps',
             'lazyviewer.source_pane.interaction.mouse',
             'lazyviewer.source_pane.interaction.events',
             'lazyviewer.file_tree_model.snapshot'):
    assert name not in sys.modules, name
""")


def test_entrypoint_restores_gc_before_dispatch_even_when_disabled_by_caller():
    run_python("""
import gc, sys, types
import lazyviewer
cli = types.ModuleType('lazyviewer.cli')
sys.modules[cli.__name__] = cli
for enabled in (True, False):
    (gc.enable if enabled else gc.disable)()
    cli.main = lambda: gc.isenabled()
    assert lazyviewer.main() is enabled
    assert gc.isenabled() is enabled
""")


def test_mouse_controller_is_created_on_demand_and_reused():
    run_python("""
import sys
from lazyviewer.source_pane import SourcePane
noop = lambda *args, **kwargs: None
pane = SourcePane(state=object(), visible_content_rows=lambda: 24,
                  move_tree_selection=noop, maybe_grow_directory_preview=noop,
                  clear_source_selection=noop, copy_selected_source_range=noop,
                  directory_preview_target_for_display_line=noop,
                  open_tree_filter=noop, apply_tree_filter_query=noop,
                  jump_to_path=noop)
pane.tick_source_selection_drag()
assert 'lazyviewer.source_pane.interaction.mouse' not in sys.modules
mouse = pane.mouse
assert pane.mouse is mouse
assert isinstance(mouse, SourcePane.SourcePaneMouseHandlers)
assert not SourcePane.SourcePaneClickResult(handled=False).handled
""")
