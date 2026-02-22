from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ContentMatch:
    path: Path
    line: int  # 1-based
    column: int  # 1-based
    preview: str


def _preview_line(text: str, max_chars: int = 220) -> str:
    clean = text.rstrip("\r\n").replace("\t", "    ")
    if len(clean) <= max_chars:
        return clean
    return clean[: max(1, max_chars - 3)] + "..."


def search_project_content_rg(
    root: Path,
    query: str,
    show_hidden: bool,
    skip_gitignored: bool = False,
    max_matches: int = 2_000,
    max_files: int = 500,
) -> tuple[dict[Path, list[ContentMatch]], bool, str | None]:
    if not query:
        return {}, False, None
    if shutil.which("rg") is None:
        return {}, False, "rg is not installed."

    root = root.resolve()
    cmd = [
        "rg",
        "--json",
        "--line-number",
        "--column",
        "--smart-case",
        "--fixed-strings",
    ]
    if not skip_gitignored:
        cmd.append("--no-ignore")
    if show_hidden:
        cmd.append("--hidden")
    cmd.extend([query, "."])

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return {}, False, f"failed to run rg: {exc}"

    matches_by_file: dict[Path, list[ContentMatch]] = {}
    total_matches = 0
    truncated = False
    stderr_text = ""
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if payload.get("type") != "match":
                continue
            data = payload.get("data", {})
            path_data = data.get("path", {})
            path_text = path_data.get("text") if isinstance(path_data, dict) else None
            if not path_text:
                continue
            relative_path = Path(path_text)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                continue
            match_path = root / relative_path

            line_number = int(data.get("line_number") or 0)
            if line_number <= 0:
                line_number = 1
            column_number = 1
            submatches = data.get("submatches")
            if isinstance(submatches, list) and submatches:
                first = submatches[0]
                if isinstance(first, dict):
                    column_number = int(first.get("start") or 0) + 1

            lines_data = data.get("lines", {})
            line_text = lines_data.get("text", "") if isinstance(lines_data, dict) else ""
            match = ContentMatch(
                path=match_path,
                line=line_number,
                column=column_number,
                preview=_preview_line(str(line_text)),
            )

            if match_path not in matches_by_file:
                if len(matches_by_file) >= max_files:
                    truncated = True
                    break
                matches_by_file[match_path] = []
            matches_by_file[match_path].append(match)

            total_matches += 1
            if total_matches >= max_matches:
                truncated = True
                break
    finally:
        if truncated and proc.poll() is None:
            proc.kill()
        _stdout_unused, stderr_text = proc.communicate()

    if proc.returncode not in (0, 1) and not matches_by_file:
        err = stderr_text.strip() or f"rg failed with exit code {proc.returncode}"
        return {}, truncated, err

    for path, items in matches_by_file.items():
        items.sort(key=lambda item: (item.line, item.column, item.preview))
        matches_by_file[path] = items

    return matches_by_file, truncated, None
