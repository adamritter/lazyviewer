"""Import and contract guards for the component architecture."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = PROJECT_ROOT / "lazyviewer"


def _absolute_import(file: Path, node: ast.ImportFrom) -> str:
    module_parts = list(file.relative_to(PROJECT_ROOT).with_suffix("").parts)
    if file.name == "__init__.py":
        module_parts = module_parts[:-1]
    else:
        module_parts = module_parts[:-1]
    if node.level:
        keep = max(0, len(module_parts) - node.level + 1)
        module_parts = module_parts[:keep]
    if node.module:
        module_parts.extend(node.module.split("."))
    return ".".join(module_parts)


def _imports(file: Path) -> set[str]:
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(_absolute_import(file, node))
    return imports


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_domain_components_do_not_depend_on_runtime_or_ui(self) -> None:
        forbidden = {
            "workspace": {"runtime", "session", "input", "render", "source_pane", "tree_pane"},
            "search": {"runtime", "session", "input", "render", "source_pane", "tree_pane"},
            "preview": {"runtime", "session", "input", "render", "source_pane", "tree_pane"},
            "session": {"runtime", "input", "render", "source_pane", "tree_pane"},
            "tree_model": {"runtime", "session", "input", "render", "source_pane", "tree_pane"},
        }
        violations: list[str] = []
        for component, blocked in forbidden.items():
            for file in (PACKAGE_ROOT / component).rglob("*.py"):
                for imported in _imports(file):
                    parts = imported.split(".")
                    if len(parts) >= 2 and parts[0] == "lazyviewer" and parts[1] in blocked:
                        violations.append(f"{file.relative_to(PROJECT_ROOT)} -> {imported}")
        self.assertEqual(violations, [])

    def test_production_code_has_no_dynamic_contract_adapters(self) -> None:
        offenders: list[str] = []
        for file in PACKAGE_ROOT.rglob("*.py"):
            text = file.read_text(encoding="utf-8")
            if "Callable[...," in text or "SimpleNamespace" in text:
                offenders.append(str(file.relative_to(PROJECT_ROOT)))
        self.assertEqual(offenders, [])

    def test_render_package_does_not_write_to_file_descriptors(self) -> None:
        offenders = [
            str(file.relative_to(PROJECT_ROOT))
            for file in (PACKAGE_ROOT / "render").rglob("*.py")
            if "os.write" in file.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
