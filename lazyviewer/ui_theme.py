"""UI theme definitions and selection helpers.

Themes are UI-only ANSI palettes (tree/help/chrome). Syntax highlighting style
for source code remains a separate setting.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UITheme:
    """Semantic ANSI palette used by renderers."""

    name: str
    divider: str
    reverse: str
    reset: str
    tree_marker: str
    tree_search_hit_text: str
    tree_dir: str
    tree_file_python: str
    tree_file_default: str
    tree_size: str
    tree_filter_query: str
    tree_filter_hint: str
    tree_picker_selected: str
    tree_workspace_active: str
    tree_workspace_inactive: str
    git_badge_changed: str
    git_badge_untracked: str
    help_heading: str
    help_key: str
    help_dim: str
    help_modal_title: str
    help_modal_border: str
    help_backdrop: str


DEFAULT_THEME = UITheme(
    name="default",
    divider="\033[2m",
    reverse="\033[7m",
    reset="\033[0m",
    tree_marker="\033[38;5;44m",
    tree_search_hit_text="\033[38;5;250m",
    tree_dir="\033[1;34m",
    tree_file_python="\033[38;5;110m",
    tree_file_default="\033[38;5;252m",
    tree_size="\033[38;5;109m",
    tree_filter_query="\033[1;38;5;81m",
    tree_filter_hint="\033[2;38;5;250m",
    tree_picker_selected="\033[38;5;81m",
    tree_workspace_active="\033[1;38;5;81m",
    tree_workspace_inactive="\033[2;38;5;250m",
    git_badge_changed="\033[38;5;214m",
    git_badge_untracked="\033[38;5;42m",
    help_heading="\033[1;38;5;81m",
    help_key="\033[38;5;229m",
    help_dim="\033[2;38;5;250m",
    help_modal_title="\033[1;38;5;45m",
    help_modal_border="\033[38;5;45m",
    help_backdrop="\033[2m",
)

OCEAN_THEME = UITheme(
    name="ocean",
    divider="\033[2;38;5;31m",
    reverse="\033[7m",
    reset="\033[0m",
    tree_marker="\033[38;5;39m",
    tree_search_hit_text="\033[38;5;153m",
    tree_dir="\033[1;38;5;45m",
    tree_file_python="\033[38;5;117m",
    tree_file_default="\033[38;5;252m",
    tree_size="\033[38;5;73m",
    tree_filter_query="\033[1;38;5;45m",
    tree_filter_hint="\033[2;38;5;110m",
    tree_picker_selected="\033[38;5;45m",
    tree_workspace_active="\033[1;38;5;45m",
    tree_workspace_inactive="\033[2;38;5;110m",
    git_badge_changed="\033[38;5;215m",
    git_badge_untracked="\033[38;5;84m",
    help_heading="\033[1;38;5;45m",
    help_key="\033[38;5;153m",
    help_dim="\033[2;38;5;110m",
    help_modal_title="\033[1;38;5;39m",
    help_modal_border="\033[38;5;39m",
    help_backdrop="\033[2;38;5;24m",
)

PLAIN_THEME = UITheme(
    name="plain",
    divider="",
    reverse="",
    reset="",
    tree_marker="",
    tree_search_hit_text="",
    tree_dir="",
    tree_file_python="",
    tree_file_default="",
    tree_size="",
    tree_filter_query="",
    tree_filter_hint="",
    tree_picker_selected="",
    tree_workspace_active="",
    tree_workspace_inactive="",
    git_badge_changed="",
    git_badge_untracked="",
    help_heading="",
    help_key="",
    help_dim="",
    help_modal_title="",
    help_modal_border="",
    help_backdrop="",
)

_THEMES: dict[str, UITheme] = {
    DEFAULT_THEME.name: DEFAULT_THEME,
    OCEAN_THEME.name: OCEAN_THEME,
}


def available_theme_names() -> tuple[str, ...]:
    """Return selectable non-plain theme names."""
    return tuple(sorted(_THEMES.keys()))


def normalize_theme_name(name: str | None) -> str:
    """Return a valid theme name, falling back to default."""
    if not name:
        return DEFAULT_THEME.name
    candidate = str(name).strip().lower()
    if not candidate:
        return DEFAULT_THEME.name
    if candidate == PLAIN_THEME.name:
        return DEFAULT_THEME.name
    if candidate in _THEMES:
        return candidate
    return DEFAULT_THEME.name


def resolve_theme(name: str | None, *, no_color: bool = False) -> UITheme:
    """Return concrete theme for requested name and color mode."""
    if no_color:
        return PLAIN_THEME
    normalized = normalize_theme_name(name)
    return _THEMES.get(normalized, DEFAULT_THEME)


__all__ = [
    "UITheme",
    "DEFAULT_THEME",
    "OCEAN_THEME",
    "PLAIN_THEME",
    "available_theme_names",
    "normalize_theme_name",
    "resolve_theme",
]
