# lazyviewer Architecture Deep Dive

## 1. Purpose and Architectural Style

`lazyviewer` is a terminal TUI for navigating a directory tree and previewing content in a split layout:

- Left pane: tree, filter/search results, picker overlays.
- Right pane: source/directory/diff/image preview.
- Bottom row: status.

The codebase is organized around a **single mutable runtime state object** (`AppState`) plus a set of focused modules that:

1. read input,
2. mutate state,
3. render state.

The architecture is intentionally callback-driven:

- The main loop (`lazyviewer/runtime/loop.py`) is wiring-heavy and generic.
- Domain logic lives in subsystem controllers (`filter_panel`, `picker_panel`, `source_pane`, `tree_pane`, `runtime/*` helpers).
- Rendering is side-effect free until final frame write.

This keeps feature logic testable and separates "what changed" from "how it is drawn."

---

## 2. Repository Topology

Primary packages and responsibilities:

- `lazyviewer/cli.py`: CLI argument parsing and launch handoff.
- `lazyviewer/runtime/*`: composition, event loop, layout, watch refresh, git jumps, terminal lifecycle, shared state.
- `lazyviewer/render/*`: frame rendering and ANSI line shaping.
- `lazyviewer/tree_pane/*`: tree model building, formatting, tree mouse interactions.
- `lazyviewer/source_pane/*`: preview generation (file/dir/diff), sticky headers, highlighting, source mouse behavior.
- `lazyviewer/filter_panel/controller.py`: file filter + content search controller.
- `lazyviewer/picker_panel/controller.py`: symbol picker + command palette + navigation ops.
- `lazyviewer/input/*`: raw terminal input decoding and mode-specific key/mouse handlers.
- `lazyviewer/search/*`: fuzzy matching and ripgrep content search.
- `lazyviewer/git_status.py`, `lazyviewer/watch.py`, `lazyviewer/gitignore.py`: git metadata and watch signatures.

---

## 3. Startup and Process Lifecycle

### 3.1 Entry points

- Script entry: `lazyviewer.py` -> `lazyviewer.cli.main`.
- Module entry: `python -m lazyviewer` -> `lazyviewer/__main__.py` -> `main`.
- Installed entrypoint (from `pyproject.toml`): `lazyviewer = lazyviewer.cli:main`.

### 3.2 CLI phase

`lazyviewer/cli.py`:

1. Parses args (`path`, `--style`, `--no-color`, `--nopager`).
2. Resolves target path.
3. Reads file content for file targets (directory targets use empty source at startup).
4. Calls `run_pager(...)` in `lazyviewer/runtime/app.py`.

### 3.3 Non-interactive fast path

In `run_pager`, if `--nopager` or stdin is not a TTY:

- optional syntax colorization (if output TTY and color enabled),
- write once to stdout,
- exit.

### 3.4 Interactive path

`run_pager` initializes:

- tree root and entries,
- preview payload for selected path,
- initial pane widths (with persisted percentages),
- `AppState`,
- all controllers/ops callbacks,
- then enters `run_main_loop(...)`.

---

## 4. The Core State Model (`AppState`)

`lazyviewer/runtime/state.py` defines a single mutable dataclass shared across the program.

State categories:

- **Navigation and selection**:
  - `current_path`, `tree_root`, `expanded`, `selected_idx`, `tree_start`.
- **Rendered preview and scrolling**:
  - `rendered`, `lines`, `start`, `text_x`, `max_start`, `wrap_text`.
- **Layout**:
  - `left_width`, `right_width`, `usable`, `browser_visible`, `show_help`.
- **Filter/picker session state**:
  - `tree_filter_*`, `picker_*`.
- **Source selection drag/copy state**:
  - `source_selection_anchor`, `source_selection_focus`.
- **Directory/image/diff preview metadata**:
  - `dir_preview_*`, `preview_image_*`, `preview_is_git_diff`.
- **Git and watches**:
  - `git_status_overlay`, `git_status_last_refresh`, `git_features_enabled`.
- **Navigation history and marks**:
  - `jump_history`, `named_marks`, `pending_mark_set`, `pending_mark_jump`.
- **Render invalidation**:
  - `dirty`, `status_message`, `status_message_until`.

Design implication: every subsystem speaks through this one state object, so cross-feature interactions are explicit and observable.

---

## 5. Runtime Composition Graph (`runtime/app.py`)

`run_pager` is the "assembly root." It wires all modules into a dependency graph using function injection and `partial(...)`.

Key composition phases:

1. **Environment + state init**
   - terminal sizing,
   - tree entries via `tree_pane.model.build_tree_entries`,
   - preview payload via `source_pane.build_rendered_for_path`,
   - initial `AppState`.

2. **Infrastructure ops**
   - `TerminalController`,
   - `PagerLayoutOps`,
   - `WatchRefreshContext`,
   - `TreeFilterIndexWarmupScheduler`.

3. **Preview refresh closures**
   - `_refresh_rendered_for_current_path`,
   - `_maybe_grow_directory_preview`,
   - `_refresh_git_status_overlay`.

4. **Controllers**
   - `TreeFilterOps` (`filter_panel/controller.py`),
   - `NavigationPickerOps` (`picker_panel/controller.py`),
   - `GitModifiedJumpDeps` (`runtime/git_jumps.py`).

5. **Mouse routing**
   - `TreeMouseHandlers` (`input/mouse.py`) combining source + tree pane mouse handlers.

6. **Keyboard ops bundle**
   - `NormalKeyOps` passed into `input/keys.handle_normal_key`.

7. **Loop callback bundle**
   - `RuntimeLoopCallbacks` passed into `runtime/loop.run_main_loop`.

An internal `NavigationProxy` breaks construction-order cycles where tree filter deps require navigation ops before they are fully instantiated.

---

## 6. Main Event Loop Mechanics (`runtime/loop.py`)

`run_main_loop` is a deterministic tick loop:

### 6.1 Per-iteration maintenance

1. Read terminal size and clamp pane widths.
2. Expire transient status message.
3. Recompute:
   - content viewport (`max_start`),
   - tree scroll window (`tree_start`),
   - picker list window (`picker_list_start`).
4. Update blink/spinner states for filter prompt UI.
5. Set `dirty` when any computed visual state changes.

### 6.2 Render phase (only if dirty)

When `state.dirty`:

1. Build `RenderContext`.
2. Call `render.render_dual_page_context`.
3. Handle kitty image placement:
   - clear previous image if geometry/path changed,
   - draw new PNG if needed.
4. Set `state.dirty = False`.

### 6.3 Input read phase

- Read one normalized key token via `input.read_key`.
- If timeout/no key:
  - maybe refresh tree watch,
  - maybe refresh git watch,
  - periodic git overlay refresh,
  - source drag auto-scroll tick.

### 6.4 Key normalization and dispatch order

Special normalization:

- `ENTER_CR`/`ENTER_LF` handling (`skip_next_lf` prevents CRLF double-fire).
- Shift resize and pending mark modes handled before general key dispatch.

Dispatch precedence:

1. pane resize hotkeys,
2. pending mark set/jump handlers,
3. ALT history hotkeys (if allowed),
4. open filter/picker overlays (`Ctrl+P`, `/`, `:`),
5. `handle_picker_key`,
6. `handle_tree_filter_key`,
7. `handle_normal_key`.

Any stage can consume the key. Quit occurs when picker/normal handlers return quit signal.

---

## 7. Input Layer

## 7.1 Raw decode (`input/reader.py`)

`read_key(fd, timeout_ms)` decodes:

- printable UTF-8 chars,
- control keys (`CTRL_*`, backspace, tab, enter),
- arrow/modified arrows (`SHIFT_LEFT`, `ALT_RIGHT`, etc.),
- SGR mouse tokens (`MOUSE_LEFT_DOWN:col:row`, wheel events).

A short ESC timeout distinguishes lone `Esc` from escape sequences.

## 7.2 Keyboard behavior (`input/keys.py`)

Three mode handlers:

- `handle_picker_key`,
- `handle_tree_filter_key`,
- `handle_normal_key`.

`handle_normal_key` includes:

- vim-like movement (`h/j/k/l`, paging, `g/G`, counts),
- tree actions (open/collapse/toggle),
- wrap/tree/help toggles,
- root changes (`r`, `R`),
- marks (`m{key}`, `'{key}`),
- git jumps (`n/N/p`) when enabled,
- external editor launch,
- quit.

The file uses small `KeyComboRegistry` dispatchers to keep key maps composable.

## 7.3 Mouse orchestration (`input/mouse.py`)

`TreeMouseHandlers` composes:

- `SourcePaneMouseHandlers` (drag-select, click-intent in source pane),
- `TreePaneMouseHandlers` (tree row clicks, arrow toggles, double-click activation).

Top-level wheel routing:

- vertical wheel over tree => move selection,
- vertical wheel over source => scroll content,
- horizontal wheel over source => x-scroll.

---

## 8. Rendering Pipeline

## 8.1 ANSI primitives (`render/ansi.py`)

Foundational functions:

- character width calculation with tab stop + East Asian width handling,
- ANSI-safe clipping and slicing,
- ANSI-preserving wrapping,
- `build_screen_lines` (logical rendered text -> display rows).

These are reused by both panes and highlighting modules.

## 8.2 Frame compositor (`render/__init__.py`)

`render_dual_page(...)`:

1. Clears frame (`\x1b[H\x1b[J`).
2. Computes help panel row reservation.
3. Chooses text-only vs split-pane mode.
4. Constructs:
   - `TreePaneRenderer` for left pane,
   - `SourcePaneRenderer` for right pane.
5. Renders content rows + help rows.
6. Builds inverted status line with location + help hint.
7. Writes one atomic frame to stdout.

## 8.3 Help overlay (`render/help.py`)

- contextual inline help rows (normal vs content-search edit/hit mode),
- full-screen modal help renderer (`render_help_page`).

---

## 9. Tree Pane Architecture

## 9.1 Data model (`tree_pane/model.py`)

`TreeEntry` is the left-pane row model:

- normal path rows (`kind="path"`),
- synthetic content-hit rows (`kind="search_hit"` with `line`/`column`/`display`).

Builders:

- `build_tree_entries` for unfiltered tree,
- `filter_tree_entries_for_files` for file-query mode,
- `filter_tree_entries_for_content_matches` for content-hit mode.

All builders maintain directory-first ordering and preserve expanded-directory context.

## 9.2 Tree rendering (`tree_pane/rendering.py`)

`TreePaneRenderer` renders:

- optional filter prompt row,
- optional picker overlay rows,
- regular tree rows with selection reverse-video,
- file size labels and git status badges.

`_format_tree_filter_status` shows loading spinner, match counts, and truncation.

## 9.3 Tree mouse semantics (`tree_pane/events.py`)

- clicking filter query row enters edit mode,
- clicking directory arrow toggles expansion immediately,
- single click selects + previews,
- double click activates:
  - directory toggles,
  - file copies basename,
  - active filter mode triggers selection activation.

---

## 10. Source Pane Architecture

The source pane is a layered pipeline:

1. choose preview payload (`path.py` + `directory.py` + `diff.py` + `syntax.py`),
2. map source/display lines (`source.py`, `diffmap.py`),
3. apply overlays (`highlighting.py`, `sticky.py`),
4. render rows (`renderer.py`),
5. route source clicks/drags (`mouse.py`, `events.py`).

## 10.1 Path-to-preview resolution (`source_pane/path.py`)

For files:

1. PNG signature => image preview metadata.
2. binary detection (NUL probe) => binary placeholder.
3. optional git diff preview (if enabled and available).
4. source text (sanitized, optionally colorized).

For directories:

- delegate to `build_directory_preview`.

Result object: `RenderedPath(text, is_directory, truncated, image_path, image_format, is_git_diff_preview)`.

## 10.2 Directory preview (`source_pane/directory.py`)

Features:

- depth/entry-bounded recursive tree preview,
- optional hidden/gitignored filtering,
- optional size labels,
- git badges per row,
- top-of-file doc summary extraction for file rows,
- LRU cache keyed by root+mtime+overlay signature+options.

## 10.3 Diff preview (`source_pane/diff.py`)

- obtains hunks via git diff against HEAD (plus staged/unstaged fallback),
- parses hunk metadata + removed lines,
- merges into full-file annotated preview:
  - unchanged lines prefixed with space marker semantics,
  - added lines with greenish background,
  - removed lines inserted with redish background,
- preserves syntax coloring and boosts low-contrast foreground on diff backgrounds,
- memoized with cache key including file mtime/size + git signature + style/color flags.

## 10.4 Syntax and text safety (`source_pane/syntax.py`)

- robust text read fallback encodings,
- sanitizes control bytes into visible escapes,
- highlighting strategy:
  - Pygments first,
  - tokenizer fallback,
  - raw source as final fallback.

## 10.5 Line mapping + overlays

- `source.py`: maps between display rows and logical source lines (including wrapped and diff previews).
- `diffmap.py`: treats removed diff lines as non-advancing source lines.
- `highlighting.py`: ANSI-preserving query highlighting + source selection background overlays.
- `sticky.py`: sticky symbol scope logic and header row generation.
- `text.py`: ANSI-aware widths, underline helpers, scroll percent.

## 10.6 Source click/drag interactions

`source_pane/mouse.py`:

- drag-select with auto-scroll at pane edges (vertical + horizontal),
- copy selected range to clipboard on release,
- delegates click intent handling.

`source_pane/events.py` click intent priority:

1. directory preview row jump,
2. import target resolution jump (`import` / `from ... import ...`),
3. token-under-cursor content search bootstrap.

---

## 11. Filter Panel (File Filter + Content Search)

`filter_panel/controller.py` (`TreeFilterOps`) owns session semantics:

- open/close mode transitions (`files` vs `content`),
- query application,
- tree rebuild and selection coercion,
- match counters and truncation flags,
- loading indicator timing,
- content search cache with bounded LRU.

Important behavior:

- file mode builds a filtered directory/file projection using fuzzy labels.
- content mode builds file nodes + synthetic hit nodes from ripgrep matches.
- content mode maintains collapsed directories separately (`tree_filter_collapsed_dirs`).
- `Enter` in content mode keeps search session active (not an automatic close).
- `Esc` can restore original location via stored `tree_filter_origin`.

---

## 12. Picker Panel and Navigation Controller

`picker_panel/controller.py` (`NavigationPickerOps`) unifies:

- symbol picker open/match/activate,
- command palette open/match/dispatch,
- jump history and named marks,
- reroot and visibility toggles,
- wrap/help/tree mode toggles,
- jump-to-path / jump-to-line operations.

It also manages picker/browser visibility transitions and selection windowing state.

---

## 13. Search Subsystems

## 13.1 Fuzzy/file search (`search/fuzzy.py`)

- project file and label collection (`rg --files` preferred, `os.walk` fallback),
- caching by `(root, show_hidden, skip_gitignored)`,
- strict substring mode for huge projects,
- fuzzy fallback scoring for smaller sets.

## 13.2 Content search (`search/content.py`)

- runs ripgrep JSON mode,
- parses match events into `ContentMatch(path,line,column,preview)`,
- guards against path traversal/absolute paths from tool output,
- enforces match/file caps and returns truncation flag + optional error.

---

## 14. Git and Watch Integration

## 14.1 Git status overlay (`git_status.py`)

- parses porcelain output,
- computes path flags (`changed`, `untracked`),
- propagates flags to ancestor directories under current tree root,
- provides badge formatter for tree rows.

## 14.2 Watch signatures (`watch.py`)

- `build_tree_watch_signature`: hashes visible directory metadata under expanded dirs.
- `build_git_watch_signature`: hashes git control files (`HEAD`, refs, index, etc.).
- `resolve_git_paths`: finds repo root and git dir.

## 14.3 Runtime refresh policy (`runtime/watch_refresh.py`)

- debounced polling intervals for tree/git signatures,
- refresh and rebuild only on signature change,
- ensures git diff previews are re-rendered when repo state changes.

## 14.4 Git navigation (`runtime/git_jumps.py`)

`n` / `N` / `p` behavior:

1. prefer intra-file diff block jumps when in diff preview,
2. otherwise navigate among modified files in tree order,
3. wrap with user-visible status messages.

---

## 15. Terminal and External Process Integration

## 15.1 Terminal control (`runtime/terminal.py`)

- raw mode + alternate screen lifecycle,
- mouse reporting toggles,
- kitty graphics clear/draw protocol helpers.

## 15.2 External editor and lazygit

- editor launch (`runtime/editor.py`) temporarily exits TUI mode,
- lazygit launch in `runtime/app.py` does same and resyncs tree/preview after exit.

## 15.3 Clipboard copy

`runtime/app.py` tries platform commands in order:

- macOS: `pbcopy`,
- Windows: `clip`,
- Linux: `wl-copy`, `xclip`, `xsel`.

Used for selected source text copy and filename copy on tree double-click file rows.

---

## 16. Persistence and User Preferences

`runtime/config.py` stores JSON in `~/.config/lazyviewer.json`:

- pane width percentages (normal mode + content-search mode),
- hidden-file preference,
- named marks (`JumpLocation` payloads).

All reads/writes are defensive (malformed or missing config is non-fatal).

---

## 17. Cache and Performance Strategy

Multiple bounded caches reduce repeated heavy work:

- directory preview LRU (`source_pane/directory.py`),
- diff preview LRU (`source_pane/diff.py`),
- symbol context LRU (`source_pane/symbols.py`),
- project file list/label caches (`search/fuzzy.py`),
- content search query cache (`filter_panel/controller.py`).

Other performance controls:

- adaptive match limits by query length,
- strict substring-only fast path for huge file sets,
- background index warmup thread (`runtime/index_warmup.py`),
- spinner/loading timers decoupled from expensive calls.

---

## 18. End-to-End Interaction Traces

## 18.1 Startup

`cli.main` -> `run_pager` -> build `AppState` -> wire ops/callbacks -> `run_main_loop` -> render frame.

## 18.2 `/` content search flow

`runtime.loop` detects `/` -> `TreeFilterOps.open_tree_filter("content")` ->
typing keys handled in `handle_tree_filter_key` -> `apply_tree_filter_query` ->
`search_project_content_rg` (cached) -> tree rebuilt with `search_hit` entries ->
preview selection updates from selected hit.

## 18.3 Source click token search flow

mouse event decoded in `input.reader` -> routed by `input.mouse` ->
`SourcePaneMouseHandlers` click -> `source_pane.events.handle_preview_click` ->
token extracted -> opens content filter and applies query.

## 18.4 Git jump flow

`n/N/p` in normal mode -> `input.keys.handle_normal_key` ->
`GitModifiedJumpDeps.jump_to_next_git_modified` ->
intra-diff block jump or modified-file jump ->
path/scroll state updated and optional wrap status message.

---

## 19. Architectural Invariants

Core invariants maintained throughout code:

- `AppState` is the only mutable cross-subsystem truth.
- Rendering reads state and does not perform business-logic decisions.
- Input handlers mutate state but rely on injected callbacks for external side effects.
- Tree and source panes are independently renderable but synchronized through shared selection/path state.
- Watch and git refreshes are poll-based and signature-driven, not event-driven.
- Non-critical failures (config I/O, optional tools, parser availability) degrade gracefully instead of crashing.

---

## 20. High-Level Dependency Direction

A simplified dependency direction:

- `cli` -> `runtime.app`
- `runtime.loop` -> `input`, `render`, callback interfaces
- `runtime.app` -> assembles `filter_panel`, `picker_panel`, `source_pane`, `tree_pane`, `search`, `git/watch`
- `render` -> `tree_pane.rendering` + `source_pane.renderer`
- `source_pane` and `tree_pane` use lower-level utilities (`render.ansi`, `git_status`, `gitignore`, `search` models)

This keeps runtime orchestration centralized while allowing leaf modules to stay focused and testable.
