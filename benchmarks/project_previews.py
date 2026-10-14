#!/usr/bin/env python3
"""Audit every visible preview in a real directory, including viewport styling.

Measures actual completed preview work, not a scheduling call or placeholder.
The threshold is configurable; a failing path is never removed from the report.
"""
from __future__ import annotations
import argparse
import gc
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lazyviewer.source_pane import SourcePane
from lazyviewer.source_pane.lazy_syntax import prepare_interactive_syntax
from lazyviewer.source_pane.renderer import SourcePaneRenderer
from lazyviewer.render.ansi import build_screen_lines
from lazyviewer.tree_model.build import build_tree_entries
from lazyviewer.runtime.gc_control import defer_cyclic_gc


def measure(target, args):
    start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    payload = SourcePane.build_rendered_for_path(target, False, 'monokai', False,
        dir_max_entries=max(1, args.rows-4), dir_skip_gitignored=True, lazy_syntax=True)
    built = time.perf_counter_ns()
    lines = build_screen_lines(payload.text, args.columns)
    renderer = SourcePaneRenderer(lines, 0, args.rows, args.columns, target,
        False, 0, '', 0, 0, payload.is_git_diff_preview, None, None)
    for row in range(args.rows):
        renderer.render_row(row)
    end = time.perf_counter_ns()
    return {'build_ms': (built-start)/1e6, 'total_ms': (end-start)/1e6,
            'cpu_ms':(time.process_time_ns()-cpu_start)/1e6,
            'directory': payload.is_directory, 'lines':len(lines),
            'pending_highlighting':bool(getattr(lines,'needs_highlighting',False))}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--budget-ms', type=float, default=5)
    p.add_argument('--rows', type=int, default=35)
    p.add_argument('--columns', type=int, default=79)
    p.add_argument('--limit', type=int)
    p.add_argument('--report-only', action='store_true')
    args=p.parse_args();root=args.root.resolve()
    paths=[root]
    for parent, dirs, files in os.walk(root):
        dirs[:]=sorted(d for d in dirs if not d.startswith('.'))
        paths.extend(Path(parent)/d for d in dirs)
        paths.extend(Path(parent)/f for f in sorted(files) if not f.startswith('.'))
    if args.limit: paths=paths[:args.limit]
    start=time.perf_counter_ns();prepare_interactive_syntax()
    build_tree_entries(root, {root}, False, skip_gitignored=True)
    startup_ms=(time.perf_counter_ns()-start)/1e6
    report={'root':str(root),'rows':args.rows,'columns':args.columns,
            'budget_ms':args.budget_ms,'startup_ms':startup_ms,'previews':[]}
    with patch('lazyviewer.source_pane.path.os.isatty', return_value=True), defer_cyclic_gc():
        for index,path in enumerate(paths):
            item={'path':str(path.relative_to(root))}
            try:
                item['first']=measure(path,args)
                item['repeat']=measure(path,args)
            except Exception as error:
                item['error']=repr(error)
            report['previews'].append(item)
            if (index+1)%1000==0:
                print(f'{index+1}/{len(paths)} previews', flush=True)
                gc.collect()  # Simulate idle collection between audit batches.
    failures=[r for r in report['previews'] if 'error' in r or
        max(r['first']['total_ms'],r['repeat']['total_ms'])>=args.budget_ms]
    report['failures']=len(failures)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2))
    print(f'{len(paths)} previews; {len(failures)} exceed {args.budget_ms:g} ms or errored')
    for r in sorted((r for r in report['previews'] if 'error' not in r),
                    key=lambda r:r['first']['total_ms'], reverse=True)[:30]:
        print(f"{r['first']['total_ms']:9.3f} first {r['repeat']['total_ms']:9.3f} repeat {r['path']}")
    if failures and not args.report_only: raise SystemExit(1)

if __name__=='__main__':main()
