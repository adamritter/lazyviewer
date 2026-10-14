"""Compatibility module for filter matching patch points."""

def __getattr__(name):
    if name in {"ContentMatch", "search_project_content_rg"}:
        from ....search import content
        return getattr(content, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ContentMatch",
    "search_project_content_rg",
]
