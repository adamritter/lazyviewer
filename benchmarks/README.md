# Terminal navigation benchmark

This runs the checkout in a real PTY connected to tui-test's headless Ghostty
terminal emulator. It checks **rendered reverse-video selection** and **rendered
preview markers**, not just output bytes or internal application state. No GUI or
installed `lazyviewer` command is needed.

## Run

From the repository root, using Python 3.10+:

```sh
uv venv .venv-bench
uv pip install --python .venv-bench/bin/python -r benchmarks/requirements.txt -e .
.venv-bench/bin/python benchmarks/navigation.py --output benchmarks/results/baseline
```

The output directory must not already exist. A full run uses four temporary
fixtures (small/large Python files, with/without modified tracked Git files).
Each has 31 files by default; large files have 2000 lines. Requires `git` and PTY
access. All fixture files and Git commits are temporary; user configuration and
repositories are not modified. Global Git configuration and hooks are excluded.
A dedicated XDG config pins the pane width. Repository imports are selected with
`PYTHONPATH`, so measurements run this checkout, not an older installed copy.

Quick smoke test:

```sh
.venv-bench/bin/python benchmarks/navigation.py \
  --cases small --steps 3 --output benchmarks/results/smoke
```

Useful options: `--rate 60`, `--large-lines 5000`, `--steps 60`, `--backend alacritty`.
Use the same backend, dimensions, fixture sizes and machine for comparisons.
`--max-p95-ms 50` optionally makes the run fail when an observed selection or
preview p95 exceeds the chosen machine-specific budget. Completion timeouts,
wrong final positions, missing previews, and unsuccessful process exits always
fail the run. Do not set a universal CI timing threshold on shared machines.

## What is measured

Each fixture executes four phases in one application process:

1. `first-visit`: individually acknowledged `j` presses through new files.
2. `revisit`: individually acknowledged `k` presses over those same files.
3. `held-j`: `j` sent at the requested rate, without waiting for each preview.
4. `held-k`: the same in reverse.

`first-visit` means new **application file visits**, not cold disk cache or cold
interpreter startup. Fixture creation and directory summaries may already warm
filesystem caches. The first file and Pygments initialization are deliberately
settled before the timed phases. Revisit phases exercise whatever caches the
application actually implements; they do not assume every stage has a cache.

A small full-frame responder runs first using the same transport, screen matcher,
and polling to show the harness latency floor. Its result is reported separately,
never subtracted from application timings.

- **Selection/preview p50, p95, max:** input submission to first matching emulator
  screen observation. Preview and selection are measured independently.
- **Drain after last key:** last submission to both final selection and preview.
  This exposes the continued movement after releasing a held key.
- **Max observed pending keys:** submitted moves minus observed selection progress.
  This is a sampled lower bound; incomplete/blank repaints are not position zero.
- **Unobserved intermediate states:** held navigation can pass a position between
  polls. These states are counted explicitly, not assigned invented latencies.
  Held-run percentiles describe **observed states only**; inspect drain and pending
  counts as well.
- **Screen read, write, send lateness:** measurement overhead and deviations from
  the requested sending schedule. Defaults poll every 5 ms *plus query cost*.
- **Stage timings:** preview construction, syntax highlighting, diff construction,
  Git-root lookup, reflow and frame composition/write. Timers are installed only
  in the benchmark child, not in production modules. They are inclusive/nested;
  **do not add their durations together**. For lazy previews the syntax stage
  sums the new line-highlighting work within a frame; fully cached frames produce
  no syntax event. Previously this stage covered eager file highlighting.

The screen matcher uses one combined native query for selected filenames and
preview markers. It does not materialize thousands of cell objects per poll,
which would itself noticeably slow down input submission. Terminal output can
still expose partial frames. The matcher rejects ambiguous previews.

## Artifacts

Each output directory contains:

- `summary.md`: latency and stage timing tables.
- `results.json`: raw submissions/observations, summaries, environment/version
  metadata, and a SHA-256 fingerprint of the application's Python source.
- `<case>.cast`: asciinema recording of the tested UI interaction.
- `<case>-<phase>.txt`: final interpreted screen for each phase.
- `<case>-stages.json`: individual stage timings, including startup.
- Failure screen and partial completed-phase results if a case fails.

This measures the PTY, application and headless emulator together. It does not
measure GPU rendering, compositor latency, monitor presentation, keyboard repeat
configuration, or every possible repository layout. Check perceived smoothness
in the user's actual terminal after changes. Directory traversal, source-pane
scrolling, search, resize and live cache invalidation need additional scenarios;
this suite specifically establishes a reproducible file-navigation baseline.

## Test the harness

These tests need no terminal backend:

```sh
python -m unittest discover -s benchmarks -p 'test_*.py' -v
```

They verify screen interpretation, ambiguous/incomplete repaints, percentile
calculation and waiting for a preview after the selection has moved.

Engine API: <https://github.com/microsoft/tui-test/tree/main/bindings/python>


## Audit a real project

```sh
.venv-bench/bin/python benchmarks/project_previews.py /path/to/project \
  --output benchmarks/results/project.json --budget-ms 5
```

This recursively checks every non-hidden file/directory, first visit and immediate
repeat, at a 79-column by 35-row source viewport. It times preview construction,
line layout and visible-row rendering, and fails if any measured sample reaches
the budget. It includes binary/image/error placeholders and paged large files.
`--report-only` retains failures without exiting unsuccessfully; `--limit` is for
smoke tests only. The project is read-only.

The audit prepares common lexers and constructs the root tree first, reporting
that duration separately. Interactive startup prepares lexers after the first
readable frame. Cyclic GC is
deferred as in the interactive event loop; between audit batches it is collected
outside timed previews. The result records both wall and CPU time, and whether
syntax work remains after the initial readable frame. **This is an initial-content
budget, not a promise that all deferred syntax is finished or that pixels have
reached the monitor within 5 ms.** A separate Ghostty run measures the full input
and terminal path. OS scheduling and cold storage can still exceed any fixed
wall-time target; retain and inspect those failures rather than dropping them.

## Measure startup through the first handled key

```sh
.venv-bench/bin/python benchmarks/startup.py /path/to/project /path/to/file.py \
  --checkout /path/to/baseline-checkout --runs 7 --output before.json
.venv-bench/bin/python benchmarks/startup.py /path/to/project /path/to/file.py \
  --checkout . --runs 7 --baseline before.json --output after.json
```

Each sample launches a fresh Python process in Ghostty at 120×36 with an isolated
config and a 33% tree pane. Timing begins before the process launch. The harness
waits for a complete initial frame (target name and Help hint), sends `?`, and
waits for the help content. It reports both first-frame time and time until that
first key is handled. The default comparison fails below a 3× median improvement
for either target. `--executable ~/.local/bin/lazyviewer` checks the installed
launcher without injecting the checkout into its Python import path.

The filesystem cache is not flushed. This measures fresh application processes,
not cold storage, GPU presentation, or total completion of optional background
work. Syntax preparation starts after the readable frame; search indexing starts
when idle. Source text and key handling are available first. Garbage collection
is suspended during module imports and restored before normal dispatch, while
the existing interactive GC policy still collects during idle periods.
