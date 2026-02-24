"""Interpret clicks inside the source preview pane.

This module resolves three click intents in order:
1. navigate directory preview rows
2. jump to import targets
3. run content search for clicked token
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from ...render.ansi import ANSI_ESCAPE_RE, char_display_width
from ...render.help import help_panel_row_count
from ...runtime.state import AppState
from ...tree_model import find_content_hit_index
from ..diffmap import (
    diff_preview_logical_line_is_removed,
    diff_preview_uses_plain_markers,
    diff_source_line_for_display_index,
    iter_diff_logical_line_ranges,
)

_CLICK_SEARCH_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_TRAILING_GIT_BADGES_RE = re.compile(r"^(.*?)(?:\s(?:\[(?:M|\?)\])+)$")
_TRAILING_SIZE_LABEL_RE = re.compile(r"^(.*?)(?:\s\[\d+\sKB\])$")
_TRAILING_DOC_SUMMARY_RE = re.compile(r"^(.*?)(?:\s{2}--\s.+)$")
_FROM_IMPORT_RE = re.compile(
    r"^\s*from\s+(?P<module>\.+[A-Za-z_][A-Za-z0-9_\.]*|\.+|[A-Za-z_][A-Za-z0-9_\.]*)\s+import\s+(?P<imports>.+?)\s*$"
)
_IMPORT_RE = re.compile(r"^\s*import\s+(?P<modules>.+?)\s*$")


def _display_col_to_text_index(text: str, display_col: int) -> int:
    """Convert display column offset to string index using terminal widths."""
    if display_col <= 0:
        return 0
    col = 0
    for idx, ch in enumerate(text):
        width = char_display_width(ch, col)
        next_col = col + width
        if display_col < next_col:
            return idx
        col = next_col
    return len(text)


def _line_has_newline_terminator(line: str) -> bool:
    """Return whether line ends with CR or LF terminator."""
    return line.endswith("\n") or line.endswith("\r")


def _display_line_to_source_line(
    lines: list[str],
    wrap_text: bool,
    display_idx: int,
) -> int | None:
    """Map display row index to logical source-line index."""
    if display_idx < 0 or display_idx >= len(lines):
        return None
    if not wrap_text:
        return display_idx

    source_idx = 0
    for idx in range(display_idx):
        if _line_has_newline_terminator(lines[idx]):
            source_idx += 1
    return source_idx


def directory_preview_target_for_display_line(state: AppState, display_idx: int) -> Path | None:
    """Resolve clicked directory-preview row to filesystem path."""
    if state.dir_preview_path is None:
        return None

    source_idx = _display_line_to_source_line(state.lines, state.wrap_text, display_idx)
    if source_idx is None:
        return None

    rendered_lines = state.rendered.splitlines()
    if source_idx < 0 or source_idx >= len(rendered_lines):
        return None

    root = state.dir_preview_path.resolve()
    dirs_by_depth: dict[int, Path] = {0: root}

    for idx, raw_line in enumerate(rendered_lines):
        plain_line = ANSI_ESCAPE_RE.sub("", raw_line).rstrip("\r\n")
        target: Path | None = None
        depth = 0
        is_dir = False

        if idx == 0:
            target = root
            depth = 0
            is_dir = True
        else:
            branch_idx = plain_line.find("├─ ")
            if branch_idx < 0:
                branch_idx = plain_line.find("└─ ")
            if branch_idx >= 0:
                name_part = plain_line[branch_idx + 3 :]
                if name_part and not name_part.startswith("<error:"):
                    name_part = name_part.rstrip()
                    doc_summary_match = _TRAILING_DOC_SUMMARY_RE.match(name_part)
                    if doc_summary_match is not None:
                        name_part = doc_summary_match.group(1)
                    badge_match = _TRAILING_GIT_BADGES_RE.match(name_part)
                    if badge_match is not None:
                        name_part = badge_match.group(1)
                    size_match = _TRAILING_SIZE_LABEL_RE.match(name_part.rstrip())
                    if size_match is not None:
                        name_part = size_match.group(1)
                    name_part = name_part.rstrip()
                    is_dir = name_part.endswith("/")
                    if is_dir:
                        name_part = name_part[:-1]
                    if name_part:
                        depth = (branch_idx // 3) + 1
                        parent = dirs_by_depth.get(depth - 1, root)
                        target = (parent / name_part).resolve()

        if target is not None and is_dir:
            dirs_by_depth[depth] = target
            for existing_depth in list(dirs_by_depth):
                if existing_depth > depth:
                    del dirs_by_depth[existing_depth]

        if idx == source_idx:
            if target is None:
                return None
            return target

    return None


def _clicked_preview_token_details(
    lines: list[str],
    selection_pos: tuple[int, int],
) -> tuple[str, int, int, str, int] | None:
    """Return token and cursor context for a click in rendered preview text."""
    if not lines:
        return None

    line_idx, text_col = selection_pos
    if line_idx < 0 or line_idx >= len(lines):
        return None

    plain_line = ANSI_ESCAPE_RE.sub("", lines[line_idx]).rstrip("\r\n")
    if not plain_line:
        return None

    clicked_index = _display_col_to_text_index(plain_line, text_col)
    candidate_indices = [clicked_index]
    if clicked_index > 0:
        candidate_indices.append(clicked_index - 1)

    for candidate in candidate_indices:
        if candidate < 0 or candidate >= len(plain_line):
            continue
        for match in _CLICK_SEARCH_TOKEN_RE.finditer(plain_line):
            if match.start() <= candidate < match.end():
                token = match.group(0)
                if token:
                    return token, match.start(), match.end(), plain_line, candidate
    return None


def clicked_preview_search_token(
    lines: list[str],
    selection_pos: tuple[int, int],
) -> str | None:
    """Return token under click suitable for content-search query."""
    details = _clicked_preview_token_details(lines, selection_pos)
    if details is None:
        return None
    return details[0]


def _logical_line_start_index(lines: list[str], display_idx: int) -> int:
    """Return first display row index belonging to the same logical source line."""
    idx = max(0, min(display_idx, len(lines) - 1))
    while idx > 0 and not _line_has_newline_terminator(lines[idx - 1]):
        idx -= 1
    return idx


def _logical_line_prefix_char_count(lines: list[str], start_idx: int, line_idx: int) -> int:
    """Return character count of wrapped chunks before ``line_idx`` in one logical line."""
    if line_idx <= start_idx:
        return 0
    count = 0
    for idx in range(start_idx, line_idx):
        chunk_plain = ANSI_ESCAPE_RE.sub("", lines[idx]).rstrip("\r\n")
        count += len(chunk_plain)
    return count


def _clicked_preview_hit_anchor(
    state: AppState,
    selection_pos: tuple[int, int],
    token_start: int,
) -> tuple[Path, int | None, int | None] | None:
    """Compute preferred search-hit anchor (path/line/column) for a clicked token."""
    try:
        preferred_path = state.current_path.resolve()
    except Exception:
        preferred_path = state.current_path
    if not preferred_path.is_file():
        return None

    line_idx, _text_col = selection_pos
    if line_idx < 0 or line_idx >= len(state.lines):
        return preferred_path, None, None

    if state.preview_is_git_diff:
        source_line = diff_source_line_for_display_index(
            state.lines,
            line_idx,
            state.wrap_text,
        )
        if state.wrap_text:
            start_idx = 0
            for range_start, range_end in iter_diff_logical_line_ranges(state.lines, True):
                if range_start <= line_idx <= range_end:
                    start_idx = range_start
                    break
        else:
            start_idx = line_idx
        prefix_chars = _logical_line_prefix_char_count(state.lines, start_idx, line_idx)
        column = prefix_chars + token_start + 1

        use_plain_markers = diff_preview_uses_plain_markers(state.lines, state.wrap_text)
        if use_plain_markers:
            first_chunk = state.lines[start_idx]
            if diff_preview_logical_line_is_removed(first_chunk, use_plain_markers=True):
                return preferred_path, None, None
            column = max(1, column - 2)
        return preferred_path, source_line, column

    source_idx = _display_line_to_source_line(state.lines, state.wrap_text, line_idx)
    source_line = source_idx + 1 if source_idx is not None else None
    start_idx = _logical_line_start_index(state.lines, line_idx) if state.wrap_text else line_idx
    prefix_chars = _logical_line_prefix_char_count(state.lines, start_idx, line_idx)
    column = prefix_chars + token_start + 1
    return preferred_path, source_line, column


def _tree_view_rows(state: AppState) -> int:
    """Return visible row count in the tree pane for current UI state."""
    help_rows = help_panel_row_count(
        state.usable,
        state.show_help,
        browser_visible=state.browser_visible,
        tree_filter_active=state.tree_filter_active,
        tree_filter_mode=state.tree_filter_mode,
        tree_filter_editing=state.tree_filter_editing,
    )
    content_rows = max(1, state.usable - help_rows)
    if state.tree_filter_active and not state.picker_active:
        return max(1, content_rows - 1)
    return content_rows


def _center_tree_selection(state: AppState) -> None:
    """Center selected tree entry in viewport when possible."""
    if not state.tree_entries:
        state.tree_start = 0
        return
    rows = _tree_view_rows(state)
    max_tree_start = max(0, len(state.tree_entries) - rows)
    centered = max(0, state.selected_idx - max(1, rows // 2))
    state.tree_start = max(0, min(centered, max_tree_start))


def _resolve_module_spec_to_path(
    state: AppState,
    module_spec: str,
) -> Path | None:
    """Resolve Python import module spec (absolute/relative) to a local file."""
    if not module_spec:
        return None
    if not state.current_path.resolve().is_file():
        return None

    current_file = state.current_path.resolve()
    module_base: Path
    if module_spec.startswith("."):
        leading_dots = len(module_spec) - len(module_spec.lstrip("."))
        relative_part = module_spec[leading_dots:]
        module_base = current_file.parent
        for _ in range(max(0, leading_dots - 1)):
            module_base = module_base.parent
        if relative_part:
            module_base = module_base.joinpath(*[part for part in relative_part.split(".") if part])
    else:
        module_base = state.tree_root.resolve().joinpath(*[part for part in module_spec.split(".") if part])

    candidates: list[Path] = []
    if module_base.suffix == ".py":
        candidates.append(module_base)
    else:
        candidates.append(module_base.with_suffix(".py"))
        candidates.append(module_base / "__init__.py")
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved.exists() and resolved.is_file():
            return resolved
    return None


def clicked_preview_import_target(
    state: AppState,
    lines: list[str],
    selection_pos: tuple[int, int],
) -> Path | None:
    """Resolve clicked token to import target path when click is on import syntax."""
    details = _clicked_preview_token_details(lines, selection_pos)
    if details is None:
        return None
    token, token_start, _token_end, plain_line, clicked_index = details
    if not token:
        return None

    line_without_comment = plain_line.split("#", 1)[0].rstrip()
    if not line_without_comment:
        return None

    module_candidates: list[str] = []
    from_match = _FROM_IMPORT_RE.match(line_without_comment)
    if from_match is not None:
        module_part = from_match.group("module")
        if from_match.start("module") <= clicked_index < from_match.end("module"):
            module_candidates.append(module_part)
        elif from_match.start("imports") <= clicked_index < from_match.end("imports"):
            import_prefix = line_without_comment[from_match.start("imports") : token_start]
            if not import_prefix.rstrip().endswith(" as"):
                if module_part.startswith("."):
                    module_candidates.append(f"{module_part}{token}")
                else:
                    module_candidates.append(f"{module_part}.{token}")
                module_candidates.append(module_part)

    import_match = _IMPORT_RE.match(line_without_comment)
    if import_match is not None and import_match.start("modules") <= clicked_index < import_match.end("modules"):
        left = clicked_index
        right = clicked_index
        while left > import_match.start("modules") and (
            line_without_comment[left - 1].isalnum() or line_without_comment[left - 1] in "._"
        ):
            left -= 1
        while right < import_match.end("modules") and (
            line_without_comment[right].isalnum() or line_without_comment[right] in "._"
        ):
            right += 1
        module_expr = line_without_comment[left:right].strip(".")
        if module_expr:
            module_candidates.append(module_expr)

    seen_modules: set[str] = set()
    for module_spec in module_candidates:
        if module_spec in seen_modules:
            continue
        seen_modules.add(module_spec)
        target = _resolve_module_spec_to_path(state, module_spec)
        if target is not None:
            return target
    return None


def _open_content_search_for_token(
    state: AppState,
    query: str,
    open_tree_filter: Callable[[str], None],
    apply_tree_filter_query: Callable[..., None],
    preferred_hit_path: Path | None = None,
    preferred_hit_line: int | None = None,
    preferred_hit_column: int | None = None,
) -> bool:
    """Open content-search UI and seed query from clicked token."""
    token = query.strip()
    if not token:
        return False
    open_tree_filter("content")
    apply_tree_filter_query(
        token,
        preview_selection=False,
        select_first_file=False,
    )
    if preferred_hit_path is not None:
        selected_hit_idx = find_content_hit_index(
            state.tree_entries,
            preferred_hit_path,
            preferred_line=preferred_hit_line,
            preferred_column=preferred_hit_column,
        )
        if selected_hit_idx is None:
            selected_hit_idx = find_content_hit_index(state.tree_entries, preferred_hit_path)
        if selected_hit_idx is not None:
            state.selected_idx = selected_hit_idx
            _center_tree_selection(state)
    state.tree_filter_editing = False
    state.dirty = True
    return True


def handle_preview_click(
    state: AppState,
    selection_pos: tuple[int, int],
    *,
    directory_preview_target_for_display_line: Callable[[int], Path | None],
    clear_source_selection: Callable[[], bool],
    reset_source_selection_drag_state: Callable[[], None],
    jump_to_path: Callable[[Path], None],
    open_tree_filter: Callable[[str], None],
    apply_tree_filter_query: Callable[..., None],
) -> bool:
    """Handle a click in source pane and execute highest-priority matching action."""
    if state.dir_preview_path is not None:
        preview_target = directory_preview_target_for_display_line(selection_pos[0])
        if preview_target is not None:
            clear_source_selection()
            reset_source_selection_drag_state()
            jump_to_path(preview_target)
            state.dirty = True
            return True

    import_target = clicked_preview_import_target(state, state.lines, selection_pos)
    if import_target is not None:
        clear_source_selection()
        reset_source_selection_drag_state()
        jump_to_path(import_target)
        state.dirty = True
        return True

    token_details = _clicked_preview_token_details(state.lines, selection_pos)
    if token_details is None:
        return False
    clicked_token, token_start, _token_end, _plain_line, _clicked_index = token_details
    clear_source_selection()
    reset_source_selection_drag_state()
    preferred_hit_anchor = _clicked_preview_hit_anchor(state, selection_pos, token_start)
    return _open_content_search_for_token(
        state,
        clicked_token,
        open_tree_filter,
        apply_tree_filter_query,
        preferred_hit_path=(preferred_hit_anchor[0] if preferred_hit_anchor is not None else None),
        preferred_hit_line=(preferred_hit_anchor[1] if preferred_hit_anchor is not None else None),
        preferred_hit_column=(preferred_hit_anchor[2] if preferred_hit_anchor is not None else None),
    )
