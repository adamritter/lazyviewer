# Test Suite Layout

The test suite follows a hybrid layout:

- `tests/unit/`: fast, module-scoped tests organized to mirror the codebase.
- `tests/integration/`: multi-module user-flow tests (runtime wiring, git flows, search flows).
- `tests/regressions/`: bug and performance regression tests that should remain stable across refactors.

## Placement Rules

- Put narrowly scoped logic tests under the mirrored path in `tests/unit/`.
- Put behavior that crosses runtime/render/input boundaries under `tests/integration/`.
- Put tests added for specific historical bugs or performance ceilings under `tests/regressions/`.
- Name split test files by the UI element or surface being validated (for example `tree_pane`, `source_pane`, `preview_pane`, `status_row`) instead of generic suffixes.

## Current Unit Mirrors

- `tests/unit/cli`
- `tests/unit/file_tree_model`
- `tests/unit/input`
- `tests/unit/render`
- `tests/unit/runtime`
- `tests/unit/search`
- `tests/unit/source_pane`
- `tests/unit/tree_pane`
- `tests/unit/watch`
