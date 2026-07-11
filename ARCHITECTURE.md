# LazyViewer architecture

LazyViewer is organized as a small set of components with typed APIs. Filesystem observation, search, preview loading, presentation, session transitions, and terminal I/O have separate owners.

## Dependency direction

```text
CLI
 └─ runtime (composition, event loop, effect interpretation)
     ├─ session (feature state, actions, effects, update)
     ├─ workspace (revisioned filesystem/Git observations)
     ├─ search (typed file/content search)
     ├─ preview (semantic document loading)
     ├─ tree_pane / source_pane (UI controllers and presenters)
     ├─ tree_model (tree-row projection)
     └─ render → retained Frame surfaces → terminal driver

search ─────→ workspace
preview ────→ workspace
tree_model ─→ workspace
session ────→ preview, workspace, tree_model
```

The domain components (`workspace`, `search`, `preview`, `session`, and `tree_model`) do not import the runtime or pane packages. `tests/unit/architecture/test_boundaries.py` enforces these boundaries.

## Components and public contracts

### Workspace

`lazyviewer.workspace` is the only owner of filesystem and Git observations.

- `WorkspaceQuery` describes normalized multi-root visibility and expansion inputs.
- `WorkspaceSnapshot` is an immutable observation of all roots.
- `WorkspaceRevision` provides separate tree and Git identities.
- `WorkspaceDelta` says exactly which root sections changed.
- `WorkspaceService.snapshot`, `refresh`, and `refresh_git` are the observation API.
- `WorkspaceService.file_index` owns the revision-keyed file catalog.
- `WorkspaceWatcher` owns polling policy; Git-only polling does not mutate the tree revision.
- `workspace.tree` contains the canonical `DirectoryEntry` and `FileEntry` model.

Symlinks that resolve outside a workspace root are excluded by the canonical scanner. Overlapping and duplicate roots remain separate workspace sections.

### Search

`SearchService` owns search workers and caches. Callers use immutable contracts from `search.model`:

- `FileSearchRequest` → `FileSearchMatch`
- `ContentSearchRequest` → streaming `ContentMatchesAdded` / `ContentSearchFinished`
- `ContentSearchJob.cancel()` and `poll()`

File indexes and content results are keyed by workspace revision. The filter controller decides UI selection and projection; it does not own threads, queues, or result caches.

### Preview

`PreviewService` loads a `PreviewRequest` into a semantic `PreviewDocument`:

- `TextDocument`
- `BinaryDocument`
- `ImageDocument`
- `DirectoryDocument` with exact `DirectoryRow.path` values
- `DiffDocument` with semantic line kinds
- `ErrorDocument`

Loading has no ANSI, theme, stdout, or TTY decisions. Its bounded cache is keyed by the request, including `workspace_revision`.

`source_pane.PreviewPresenter` is the terminal presentation boundary. It applies syntax color, directory styling, Git badges, and diff backgrounds. Directory clicks use `DirectoryDocument.rows`; rendered text is never reparsed to recover paths.

`runtime.PreviewWorker` performs semantic loading in the background. Completed requests carry their revision, and stale results are rejected by the session update function.

### Session

`SessionState` composes feature-owned mutable state:

- `WorkspaceViewState`
- `PreviewViewState`
- `LayoutState`
- `FilterState`
- `PickerState`
- `GitState`
- `NavigationState`
- `InterfaceState`

Code accesses the owning feature explicitly (`state.preview.lines`, `state.filter.query`, and so on); there is no flat global state facade.

External events enter through typed actions in `session.actions`. `session.update.update(state, action)` performs the state transition and returns typed effects from `session.effects`. `SessionCoordinator` interprets effects at the runtime boundary.

Current root-level actions cover clock ticks, terminal resize, pane-width changes, workspace deltas, and completed previews. Mode-specific pane controllers still own local keyboard/navigation rules.

### Tree and source panes

`TreePane` composes three focused UI objects:

- `TreeFilterController` for filter state, result projection, and selection
- `NavigationController` / `PickerPanel` for navigation, roots, marks, and pickers
- `TreePaneMouseHandlers` for tree pointer intent

`SourcePane` owns only source geometry and mouse behavior. `PreviewController` owns request/apply and directory-budget behavior. `PreviewPresenter` owns terminal formatting.

Shared callback shapes live as small protocols in `lazyviewer.ports`; dynamic `getattr`, `SimpleNamespace`, and `Callable[..., ...]` adapters are intentionally absent.

### Rendering and terminal I/O

Rendering is pure:

1. Runtime builds a `RenderContext`.
2. `RetainedPageRenderer` compares pane-specific input keys and reuses unchanged tree, preview, and help presentations.
3. It returns an immutable `Frame` of named rectangular `Surface` values; standalone capture frames also carry the legacy canonical full-screen text.
4. `render.diff.diff_frames` compares the surfaces with the previously presented frame and returns a pure ANSI patch.
5. `TerminalController.write_frame` performs one atomic file-descriptor write.

The coarse `InterfaceState.dirty` flag schedules presentation only. Feature controllers do not carry pane-specific rendering flags; the retained renderer owns its dependency keys. A preview-only scroll therefore reuses the tree presentation, and the terminal patch addresses only changed preview rows plus the status row.

The first frame, geometry changes, and terminal re-entry derive a full repaint from the surfaces. Production rendering does not rebuild that full-screen string on ordinary updates. Stable layouts use absolute cursor positioning, style-isolated row writes, and clear-to-line-end at the right screen edge. The render package never writes to stdout. Terminal mode, mouse reporting, Kitty image commands, retained-screen state, and frame writes all belong to `TerminalController`.

## Runtime flow

Startup in `runtime.app.run_pager` is the composition root:

1. Construct `WorkspaceService`, `PreviewService`, and `PreviewPresenter`.
2. `SessionBootstrap` creates the initial workspace snapshot, tree projection, preview document, and feature states.
3. Construct layout, watchers, preview worker, pane controllers, and `SessionCoordinator`.
4. Bundle concrete components and operations into `ApplicationComponents` and `ApplicationOperations`.
5. Start the event loop.

The loop maps decoded terminal tokens to typed input commands, applies root session actions for resize/time changes, delegates mode-specific keys to pane controllers, builds a frame when dirty, and lets the terminal driver emit it.

## Cache and freshness rules

- Workspace file indexes: tree revision.
- Search results: workspace tree revision plus query/limits.
- Preview documents: full workspace revision plus request policy.
- Document summaries: file metadata, owned by `workspace.doc_summary`.
- Git polling: Git observations only; tree polling is independent.
- Rendered panes: O(1) identity and scalar keys over replace-on-change view collections.
- Presented terminal screen: the previous structured frame, invalidated on geometry or terminal lifecycle changes.

When a workspace revision changes, consumers issue a new typed request. Async results from older revisions are ignored rather than heuristically accepted.

## Testing strategy

- `tests/unit/workspace`, `search`, `preview`, and `session` test component contracts.
- `tests/unit/render` proves retained-cache isolation and applies incremental patches to a virtual screen for equivalence with full rendering.
- `tests/unit/architecture` enforces import direction, explicit contracts, and pure rendering.
- `tests/integration/runtime` drives assembled controllers and long interaction sequences.
- `tests/integration/git` validates real Git and ignore behavior.
- `tests/regressions` protects performance and randomized multi-root invariants.

Use Python 3.12 for the repository test environment:

```bash
PYENV_VERSION=3.12.3 uv run --with pytest pytest -q
```

## Extension rules

- New filesystem facts belong in `workspace` and must affect a revision when consumers need invalidation.
- New search modes start with request/result types, then a `SearchService` method.
- New preview formats add a `PreviewDocument` variant, loader branch, and presenter branch.
- New external runtime events add a session action and, when necessary, an explicit effect.
- Pane code may format or interpret UI intent; it must not acquire filesystem caches, worker queues, or terminal output responsibilities.
