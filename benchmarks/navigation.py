#!/usr/bin/env python3
"""Measure real LazyViewer navigation through tui-test's terminal emulator.

See benchmarks/README.md. No production instrumentation or installed LazyViewer
is needed: the child imports the checkout containing this script.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version

NAME = re.compile(r"file_(\d{4})\.py")
MARKER = re.compile(r"PREVIEW_(\d{4})")


def stats(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {"count": len(ordered), "p50_ms": ordered[(len(ordered)-1)//2],
            "p95_ms": ordered[math.ceil(len(ordered)*.95)-1], "max_ms": ordered[-1]}


def decode_matches(matches, cols, rows):
    """Interpret a single emulator snapshot, including reverse-video selection."""
    selected, previews = set(), set()
    for match in matches:
        if match.start.row >= rows-1:
            continue
        if match.start.column < cols//3:
            selected.update(int(m.group(1)) for m in NAME.finditer(match.text))
        else:
            previews.update(int(m.group(1)) for m in MARKER.finditer(match.text))
    return (next(iter(selected)) if len(selected) == 1 else None,
            next(iter(previews)) if len(previews) == 1 else None)


def screen_locator(term):
    from tui_test import TextStyle
    selected = term.get_by_text(NAME.pattern, regex=True).and_(
        term.get_by_style(TextStyle(inverse=True)))
    return selected.or_(term.get_by_text(MARKER.pattern, regex=True))


def child(profile_path, fixture):
    """Opt-in stage timers live only in the benchmark process."""
    import atexit
    from lazyviewer import cli
    from lazyviewer.runtime import loop
    from lazyviewer.runtime.layout import PagerLayout
    from lazyviewer.source_pane import SourcePane
    from lazyviewer.source_pane import path as preview_path
    from lazyviewer.source_pane import diff
    try:
        from lazyviewer.source_pane.lazy_syntax import LineHighlighter
    except ImportError:
        LineHighlighter = None  # Benchmark older, eager checkouts too.
    records = []

    def wrap(owner, attr, label, static=False):
        original = getattr(owner, attr)
        @functools.wraps(original)
        def timed(*args, **kwargs):
            start = time.monotonic_ns()
            try:
                return original(*args, **kwargs)
            finally:
                records.append({"stage": label, "start_ns": start,
                                "duration_ms": (time.monotonic_ns()-start)/1e6})
        setattr(owner, attr, staticmethod(timed) if static else timed)

    wrap(SourcePane, "build_rendered_for_path", "preview", static=True)
    wrap(PagerLayout, "rebuild_screen_lines", "reflow")
    wrap(preview_path, "colorize_source", "syntax")
    wrap(diff, "colorize_source", "syntax")
    wrap(preview_path, "build_unified_diff_preview_for_path", "diff")
    wrap(diff, "_resolve_repo_and_git_dir", "git_root")
    # Eager previews style whole files before the frame. Lazy previews style
    # visible lines inside it; report their summed time as one comparable event.
    syntax_ms = 0.0
    original_line = LineHighlighter._next_line if LineHighlighter is not None else None
    @functools.wraps(original_line)
    def timed_line(*args, **kwargs):
        nonlocal syntax_ms
        start = time.monotonic_ns()
        try:
            return original_line(*args, **kwargs)
        finally:
            syntax_ms += (time.monotonic_ns()-start)/1e6
    if LineHighlighter is not None:
        LineHighlighter._next_line = timed_line
    original_frame = loop.render_dual_page_context
    @functools.wraps(original_frame)
    def timed_frame(*args, **kwargs):
        start = time.monotonic_ns()
        before = syntax_ms
        try:
            return original_frame(*args, **kwargs)
        finally:
            if syntax_ms > before:
                records.append({"stage": "syntax", "start_ns": start,
                                "duration_ms": syntax_ms-before})
            records.append({"stage": "frame", "start_ns": start,
                            "duration_ms": (time.monotonic_ns()-start)/1e6})
    loop.render_dual_page_context = timed_frame
    atexit.register(lambda: Path(profile_path).write_text(json.dumps(records)))
    sys.argv = ["lazyviewer", fixture]
    cli.main()


def fixture(root, count, lines, git):
    root.mkdir()
    for i in range(count):
        # All lines carry the same marker, including the first changed line in
        # git cases. This also works when diff navigation starts mid-file.
        text = f'# PREVIEW_{i:04d}\n' + ''.join(
            f'value_{j} = ({j}, "PREVIEW_{i:04d}")\n' for j in range(lines-1))
        (root / f"file_{i:04d}.py").write_text(text)
    if git:
        def run(*args):
            subprocess.run(["git", "-C", str(root), *args], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        run("init", "-q")
        run("add", ".")
        run("-c", "user.name=Benchmark", "-c", "user.email=benchmark@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        for path in root.glob("*.py"):
            path.write_text(path.read_text().replace("# PREVIEW_", "# changed PREVIEW_", 1))


async def snapshot(locator, args):
    started = time.monotonic_ns()
    matches = await locator.locations()
    ended = time.monotonic_ns()
    selection, preview = decode_matches(matches, args.cols, args.rows)
    return {"at_ns": ended, "selection": selection, "preview": preview,
            "read_ms": (ended-started)/1e6}


async def wait_position(term, args, index):
    deadline = time.monotonic()+args.timeout
    locator = screen_locator(term)
    while time.monotonic() < deadline:
        sample = await snapshot(locator, args)
        if sample["selection"] == index and sample["preview"] == index:
            return
        await asyncio.sleep(args.poll_ms/1000)
    raise TimeoutError(f"Expected selection AND preview {index}; last screen:\n{await term.text()}")


async def phase(term, args, name, start, targets, held):
    result = {"name": name, "mode": "held" if held else "single", "start_ns": time.monotonic_ns(),
              "sent": [], "observations": [], "read_ms": []}
    key = "j" if targets[-1] > start else "k"
    locator = screen_locator(term)
    last_pair = (None, None)

    async def observe():
        nonlocal last_pair
        sample = await snapshot(locator, args)
        result["read_ms"].append(sample.pop("read_ms"))
        pair = (sample["selection"], sample["preview"])
        if pair != last_pair:
            result["observations"].append(sample)
            last_pair = pair
        return pair

    async def send(index, planned_ns):
        sent = {"index": index, "at_ns": time.monotonic_ns(), "planned_ns": planned_ns}
        result["sent"].append(sent)
        await term.write(key)
        sent["write_ms"] = (time.monotonic_ns()-sent["at_ns"])/1e6

    if held:
        async def sender():
            origin = time.monotonic_ns()
            for ordinal, index in enumerate(targets):
                planned = origin+int(ordinal*1e9/args.rate)
                await asyncio.sleep(max(0, (planned-time.monotonic_ns())/1e9))
                await send(index, planned)
        task = asyncio.create_task(sender())
        deadline = time.monotonic()+len(targets)/args.rate+args.timeout
        try:
            while time.monotonic() < deadline:
                pair = await observe()
                if task.done():
                    task.result()
                    if pair == (targets[-1], targets[-1]):
                        break
                await asyncio.sleep(args.poll_ms/1000)
            else:
                raise TimeoutError(f"{name}: queued navigation did not finish")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    else:
        for index in targets:
            await send(index, time.monotonic_ns())
            deadline = time.monotonic()+args.timeout
            while time.monotonic() < deadline:
                if await observe() == (index, index):
                    break
                await asyncio.sleep(args.poll_ms/1000)
            else:
                raise TimeoutError(f"{name}: no selection/preview for {index}")

    result["end_ns"] = time.monotonic_ns()
    result["final_screen"] = await term.text()
    for field in ("selection", "preview"):
        latencies = []
        for sent in result["sent"]:
            observed = next((o for o in result["observations"]
                             if o["at_ns"] >= sent["at_ns"] and o[field] == sent["index"]), None)
            if observed:
                latencies.append((observed["at_ns"]-sent["at_ns"])/1e6)
        result[field] = stats(latencies)
        result[field]["unobserved_intermediate_states"] = len(targets)-len(latencies)
    result["screen_read"] = stats(result.pop("read_ms"))
    result["send_lateness"] = stats([(s["at_ns"]-s["planned_ns"])/1e6 for s in result["sent"]])
    result["write"] = stats([s["write_ms"] for s in result["sent"]])
    final = next(o for o in result["observations"]
                 if o["selection"] == targets[-1] and o["preview"] == targets[-1]
                 and o["at_ns"] >= result["sent"][-1]["at_ns"])
    result["drain_after_last_key_ms"] = (final["at_ns"]-result["sent"][-1]["at_ns"])/1e6
    sequence = {index: ordinal+1 for ordinal, index in enumerate(targets)}
    backlog = []
    for observation in result["observations"]:
        sent_count = sum(s["at_ns"] <= observation["at_ns"] for s in result["sent"])
        if observation["selection"] is None:
            continue  # A partially repainted screen is not a return to index zero.
        reached = sequence.get(observation["selection"], 0)
        backlog.append(max(0, sent_count-reached))
    result["max_observed_pending_keys"] = max(backlog, default=0)
    return result


def control_child(cols, rows):
    """Minimal full-frame responder for the transport/emulator latency floor."""
    import termios
    import tty
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    index = 0
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?1049h")
        while True:
            left = f"  file_{index:04d}.py"
            lines = [f"\x1b[7m{left}\x1b[0m" + " "*(cols//3-len(left))
                     + f"│# PREVIEW_{index:04d}"]
            lines += [" "*(cols//3)+"│"]*(rows-2)
            lines += ["control"]
            sys.stdout.write("\x1b[H\x1b[J"+"\r\n".join(lines))
            sys.stdout.flush()
            key = os.read(fd, 1)
            if not key or key == b"q":
                break
            index = max(0, index+(1 if key == b"j" else -1 if key == b"k" else 0))
    finally:
        sys.stdout.write("\x1b[?1049l")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


async def control(args, work):
    from tui_test import TuiTest
    async with TuiTest.ephemeral(backend=args.backend) as term:
        await term.run(sys.executable, str(Path(__file__).resolve()), "--control-child",
                       str(args.cols), str(args.rows), cols=args.cols, rows=args.rows,
                       cwd=str(work), wait_ready=False)
        await wait_position(term, args, 0)
        result = await phase(term, args, "transport-control", 0,
                             list(range(1, min(args.steps, 20)+1)), False)
        await term.write("q")
        await term.wait_exit(timeout=5000)
        if (await term.state()).exited != 0:
            raise RuntimeError("Control process failed")
        return result


def write_report(report, output):
    (output/"results.json").write_text(json.dumps(report, indent=2))
    lines = ["# Navigation benchmark", "", f"Backend: {report['backend']}; "
             f"tui-test {report['tui_test']}; {report['cols']}×{report['rows']}; "
             f"{report['rate_hz']:g} keys/s; {report['steps']} steps.", "",
             "All times below are milliseconds. Held-run percentiles describe observed states only.", "",
             "| Case | Phase | Selection p95 | Preview p95 | Drain after last key | Max pending | Unobserved selections |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for case in report["cases"]:
        for p in case["phases"]:
            lines.append(f"| {case['case']} | {p['name']} | {p['selection']['p95_ms']:.1f} | "
                         f"{p['preview']['p95_ms']:.1f} | {p['drain_after_last_key_ms']:.1f} | "
                         f"{p['max_observed_pending_keys']} | "
                         f"{p['selection']['unobserved_intermediate_states']} |")
    c = report["control"]
    lines += ["", f"Minimal full-frame responder p95: {c['selection']['p95_ms']:.1f} ms. "
              "This is a control measurement, not a correction subtracted from results.", "",
              "## Inclusive stage timings (p95 ms)", "",
              "Nested stages overlap; do not add these columns together.", "",
              "| Case | Phase | Preview | Syntax | Diff | Git root | Reflow | Frame |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for case in report["cases"]:
        for p in case["phases"]:
            values = [p['stages'].get(s, {}).get('p95_ms') for s in
                      ('preview', 'syntax', 'diff', 'git_root', 'reflow', 'frame')]
            lines.append(f"| {case['case']} | {p['name']} | " + " | ".join(
                "—" if v is None else f"{v:.1f}" for v in values)+" |")
    (output/"summary.md").write_text("\n".join(lines)+"\n")


async def run_case(args, work, output, name):
    from tui_test import TuiTest
    large = "large" in name
    root = work / name
    fixture(root, args.steps+1, args.large_lines if large else args.small_lines, name.startswith("git-"))
    config = work / (name+"-config") / "lazyviewer"
    config.mkdir(parents=True)
    (config/"config.json").write_text('{"left_pane_percent": 33}')
    profile_path = output / (name+"-stages.json")
    env = dict(os.environ, XDG_CONFIG_HOME=str(config.parent), TERM="xterm-256color",
               PYTHONPATH=str(args.checkout))
    results = []
    async with TuiTest.ephemeral(backend=args.backend) as term:
        try:
            await term.run(sys.executable, str(Path(__file__).resolve()), "--child",
                           str(profile_path), str(root), cols=args.cols, rows=args.rows,
                           cwd=str(work), env=env, wait_ready=False)
            # Wait for the actual TUI, not a shell prompt or mere process spawn.
            deadline = time.monotonic()+args.timeout
            while time.monotonic() < deadline:
                text = await term.text()
                if "? Help" in text and "file_0000.py" in text:
                    break
                await asyncio.sleep(.01)
            else:
                raise TimeoutError("Initial LazyViewer frame did not arrive: "+text)
            await term.start_recording(str(output/(name+".cast")), format="cast")
            await term.write("j")
            await wait_position(term, args, 0)
            ascending = list(range(1, args.steps+1))
            descending = list(range(args.steps-1, -1, -1))
            for label, start, targets, held in (
                ("first-visit", 0, ascending, False),
                ("revisit", args.steps, descending, False),
                ("held-j", 0, ascending, True),
                ("held-k", args.steps, descending, True),
            ):
                result = await phase(term, args, label, start, targets, held)
                results.append(result)
                (output/(name+"-"+label+".txt")).write_text(result["final_screen"])
                print(f'{name:10} {label:11} selection p95={result["selection"]["p95_ms"]:.1f} ms '
                      f'preview p95={result["preview"]["p95_ms"]:.1f} ms '
                      f'drain={result["drain_after_last_key_ms"]:.1f} ms '
                      f'pending={result["max_observed_pending_keys"]}', flush=True)
            await term.stop_recording()
            await term.write("q")
            await term.wait_exit(timeout=5000)
            if (await term.state()).exited != 0:
                raise RuntimeError("LazyViewer exited unsuccessfully")
        except BaseException:
            (output/(name+"-failure.txt")).write_text(await term.text())
            (output/(name+"-partial.json")).write_text(json.dumps(results, indent=2))
            raise
    stages = json.loads(profile_path.read_text())
    for result in results:
        names = sorted({s["stage"] for s in stages})
        result["stages"] = {stage: stats([s["duration_ms"] for s in stages
            if s["stage"] == stage and result["start_ns"] <= s["start_ns"] < result["end_ns"]])
            for stage in names}
    return {"case": name, "file_lines": args.large_lines if large else args.small_lines,
            "phases": results}


async def benchmark(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": 1, "python": sys.version, "platform": platform.platform(),
              "tui_test": version("tui-test"), "backend": args.backend,
              "checkout": str(args.checkout), "rate_hz": args.rate, "steps": args.steps,
              "cols": args.cols, "rows": args.rows, "poll_ms": args.poll_ms, "cases": []}
    digest = hashlib.sha256()
    for source in sorted((args.checkout/"lazyviewer").rglob("*.py")):
        digest.update(str(source.relative_to(args.checkout)).encode())
        digest.update(source.read_bytes())
    report["source_sha256"] = digest.hexdigest()
    with tempfile.TemporaryDirectory(prefix="lazyviewer-navigation-") as directory:
        work = Path(directory)
        report["control"] = await control(args, work)
        # Explicitly exclude inherited global Git config/hooks from fixtures.
        old_config = os.environ.get("GIT_CONFIG_GLOBAL")
        old_system = os.environ.get("GIT_CONFIG_NOSYSTEM")
        os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
        os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
        try:
            for name in args.cases:
                report["cases"].append(await run_case(args, work, output, name))
                write_report(report, output)
        finally:
            for key, old in (("GIT_CONFIG_GLOBAL", old_config), ("GIT_CONFIG_NOSYSTEM", old_system)):
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
    failed = args.max_p95_ms is not None and any(
        p["selection"]["p95_ms"] > args.max_p95_ms or p["preview"]["p95_ms"] > args.max_p95_ms
        for case in report["cases"] for p in case["phases"])
    if failed:
        raise SystemExit("Latency budget exceeded; see results.json")


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--control-child":
        control_child(int(sys.argv[2]), int(sys.argv[3]))
        return
    if len(sys.argv) == 4 and sys.argv[1] == "--child":
        child(sys.argv[2], sys.argv[3])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New artifact directory")
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--backend", choices=("ghostty", "alacritty", "rio", "xtermjs"), default="ghostty")
    parser.add_argument("--cases", nargs="+", choices=("small", "large", "git-small", "git-large"),
                        default=["small", "large", "git-small", "git-large"])
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--rate", type=float, default=30)
    parser.add_argument("--poll-ms", type=float, default=5)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--rows", type=int, default=36)
    parser.add_argument("--small-lines", type=int, default=40)
    parser.add_argument("--large-lines", type=int, default=2000)
    parser.add_argument("--max-p95-ms", type=float, help="Optional machine-specific failure budget")
    args = parser.parse_args()
    if not 1 <= args.steps <= 9998 or min(args.rate, args.timeout, args.poll_ms) <= 0:
        parser.error("steps must be 1..9998; rate, timeout, and poll-ms must be positive")
    if args.cols < 100 or args.rows < 10 or min(args.small_lines, args.large_lines) < 2:
        parser.error("Need >=100 columns, >=10 rows, and >=2 fixture lines")
    args.checkout = args.checkout.resolve()
    asyncio.run(benchmark(args))


if __name__ == "__main__":
    main()
