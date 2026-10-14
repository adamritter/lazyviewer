#!/usr/bin/env python3
"""Measure fresh-process startup to a real, interactive Ghostty frame."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from importlib.metadata import version

async def sample(checkout, target, work, executable=None):
    from tui_test import TuiTest
    config=work/'config'/'lazyviewer';config.mkdir(parents=True,exist_ok=True)
    (config/'config.json').write_text('{"left_pane_percent":33}')
    env=dict(os.environ,PYTHONPATH=str(checkout),XDG_CONFIG_HOME=str(config.parent),TERM='xterm-256color')
    command = [sys.executable, '-m', 'lazyviewer']
    if executable is not None:
        command = [str(executable)]
        env.pop('PYTHONPATH', None)
    async with TuiTest.ephemeral(backend='ghostty') as terminal:
        start=time.perf_counter_ns()
        await terminal.run(*command,str(target),cols=120,rows=36,
                           cwd=str(work),env=env,wait_ready=False)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            text=await terminal.text()
            if '? Help' in text and target.name in text:
                break
            await asyncio.sleep(.001)
        else:
            raise TimeoutError('No complete initial frame: '+await terminal.text())
        frame=time.perf_counter_ns()
        await terminal.write('?')
        while time.monotonic()<deadline:
            help_text=await terminal.text()
            if 'quit' in help_text:
                break
            await asyncio.sleep(.001)
        else:
            raise TimeoutError('First frame did not respond to help key')
        responsive=time.perf_counter_ns()
        await terminal.write('q')
        await terminal.wait_exit(timeout=5000)
        if (await terminal.state()).exited!=0:
            raise RuntimeError('App did not exit cleanly')
        return {'frame_ms':(frame-start)/1e6, 'first_key_ms':(responsive-frame)/1e6,
                'usable_ms':(responsive-start)/1e6}

async def main(args):
    checkout=args.checkout.resolve()
    digest = hashlib.sha256()
    for path in sorted((checkout/'lazyviewer').rglob('*.py')):
        digest.update(str(path.relative_to(checkout)).encode())
        digest.update(path.read_bytes())
    report={'checkout':str(checkout),'source_sha256':digest.hexdigest(),
            'executable':str(args.executable) if args.executable else None,
            'python':sys.version,'tui_test':version('tui-test'),
            'backend':'ghostty','runs':args.runs,'cases':[]}
    with tempfile.TemporaryDirectory(prefix='lv-startup-benchmark-') as temp:
        work=Path(temp)
        for target in args.targets:
            results=[]
            for _ in range(args.runs):
                results.append(await sample(checkout,target.resolve(),work,args.executable))
            case={'target':str(target.resolve()),'samples':results,
                  'median_frame_ms':statistics.median(r['frame_ms'] for r in results),
                  'median_usable_ms':statistics.median(r['usable_ms'] for r in results),
                  'max_first_key_ms':max(r['first_key_ms'] for r in results)}
            report['cases'].append(case)
            print(f"{target}: frame={case['median_frame_ms']:.1f} ms; usable={case['median_usable_ms']:.1f} ms; first-key max={case['max_first_key_ms']:.1f} ms",flush=True)
    if args.baseline:
        baseline=json.loads(args.baseline.read_text())
        for case in report['cases']:
            old=next(c for c in baseline['cases'] if c['target']==case['target'])
            case['speedup']=old['median_usable_ms']/case['median_usable_ms']
            print(f"{case['target']}: {case['speedup']:.2f}x usable-startup speedup",flush=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2))
    if args.baseline and any(c['speedup']<args.min_speedup for c in report['cases']):
        raise SystemExit('Startup speedup target not met')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('targets',nargs='+',type=Path)
    p.add_argument('--checkout',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--runs',type=int,default=7)
    p.add_argument('--executable',type=Path,help='Measure an installed launcher instead of the checkout')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--baseline',type=Path)
    p.add_argument('--min-speedup',type=float,default=3)
    args=p.parse_args()
    if args.runs<3:p.error('Use at least three fresh processes per target')
    asyncio.run(main(args))
