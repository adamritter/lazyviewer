from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer import symbols


def line_col_for(text: str, token: str) -> tuple[int, int]:
    start = text.index(token)
    line = text.count("\n", 0, start)
    line_start = text.rfind("\n", 0, start)
    column = start if line_start < 0 else start - line_start - 1
    return line, column


class FakeNode:
    def __init__(
        self,
        node_type: str,
        start_byte: int,
        end_byte: int,
        start_point: tuple[int, int],
        *,
        named_children: list["FakeNode"] | None = None,
        fields: dict[str, "FakeNode"] | None = None,
    ) -> None:
        self.type = node_type
        self.start_byte = start_byte
        self.end_byte = end_byte
        self.start_point = start_point
        self.named_children = named_children or []
        self._fields = fields or {}

    def child_by_field_name(self, name: str):
        return self._fields.get(name)


class FakeTree:
    def __init__(self, root_node: FakeNode) -> None:
        self.root_node = root_node


class FakeParser:
    def __init__(self, tree: FakeTree) -> None:
        self._tree = tree

    def parse(self, _source_bytes: bytes) -> FakeTree:
        return self._tree


class SymbolsBehaviorTests(unittest.TestCase):
    def test_collect_symbols_requires_file_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out, error = symbols.collect_symbols(Path(tmp))
        self.assertEqual(out, [])
        self.assertEqual(error, "Symbol outline is available for files only.")

    def test_collect_symbols_rejects_unsupported_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.xyz"
            path.write_text("hello", encoding="utf-8")
            out, error = symbols.collect_symbols(path)
        self.assertEqual(out, [])
        self.assertEqual(error, "No Tree-sitter grammar configured for .xyz.")

    def test_collect_symbols_surfaces_parser_load_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text("def f():\n    return 1\n", encoding="utf-8")
            with mock.patch("lazyviewer.symbols._load_parser", return_value=(None, "boom")):
                out, error = symbols.collect_symbols(path)
        self.assertEqual(out, [])
        self.assertEqual(error, "boom")

    def test_collect_symbols_extracts_and_sorts_symbols(self) -> None:
        source = (
            "import os\n"
            "class Zed:\n"
            "    pass\n"
            "def alpha():\n"
            "    return 1\n"
            "@decorator\n"
            "def beta():\n"
            "    return 2\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(source, encoding="utf-8")

            import_start = source.index("import os")
            import_line, import_col = line_col_for(source, "import os")
            import_node = FakeNode(
                "import_statement",
                import_start,
                import_start + len("import os"),
                (import_line, import_col),
            )

            class_stmt_start = source.index("class Zed:")
            class_line, class_col = line_col_for(source, "class Zed:")
            class_name_start = source.index("Zed")
            class_name_line, class_name_col = line_col_for(source, "Zed")
            class_name = FakeNode(
                "identifier",
                class_name_start,
                class_name_start + len("Zed"),
                (class_name_line, class_name_col),
            )
            class_node = FakeNode(
                "class_definition",
                class_stmt_start,
                class_stmt_start + len("class Zed:"),
                (class_line, class_col),
                fields={"name": class_name},
            )

            alpha_stmt_start = source.index("def alpha():")
            alpha_line, alpha_col = line_col_for(source, "def alpha():")
            alpha_name_start = source.index("alpha")
            alpha_name_line, alpha_name_col = line_col_for(source, "alpha")
            alpha_name = FakeNode(
                "identifier",
                alpha_name_start,
                alpha_name_start + len("alpha"),
                (alpha_name_line, alpha_name_col),
            )
            alpha_node = FakeNode(
                "function_definition",
                alpha_stmt_start,
                alpha_stmt_start + len("def alpha():"),
                (alpha_line, alpha_col),
                fields={"name": alpha_name},
            )

            beta_stmt_start = source.index("def beta():")
            beta_line, beta_col = line_col_for(source, "def beta():")
            beta_name_start = source.index("beta")
            beta_name_line, beta_name_col = line_col_for(source, "beta")
            beta_name = FakeNode(
                "identifier",
                beta_name_start,
                beta_name_start + len("beta"),
                (beta_name_line, beta_name_col),
            )
            beta_node = FakeNode(
                "function_definition",
                beta_stmt_start,
                beta_stmt_start + len("def beta():"),
                (beta_line, beta_col),
                fields={"name": beta_name},
            )

            decorated_start = source.index("@decorator")
            decorated_line, decorated_col = line_col_for(source, "@decorator")
            decorated_node = FakeNode(
                "decorated_definition",
                decorated_start,
                beta_stmt_start + len("def beta():"),
                (decorated_line, decorated_col),
                fields={"definition": beta_node},
            )

            root = FakeNode(
                "module",
                0,
                len(source),
                (0, 0),
                named_children=[alpha_node, decorated_node, import_node, class_node],
            )
            parser = FakeParser(FakeTree(root))

            with mock.patch("lazyviewer.symbols._load_parser", return_value=(parser, None)), mock.patch(
                "lazyviewer.symbols.read_text", return_value=source
            ):
                out, error = symbols.collect_symbols(path)

        self.assertIsNone(error)
        self.assertEqual([entry.kind for entry in out], ["import", "class", "fn", "fn"])
        self.assertEqual([entry.name for entry in out], ["import os", "Zed", "alpha", "beta"])
        self.assertTrue(out[0].label.startswith("import"))
        self.assertTrue(out[1].label.endswith("Zed"))

    def test_collect_symbols_respects_max_symbols_limit(self) -> None:
        source = "import os\nclass A:\n    pass\ndef f():\n    return 1\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(source, encoding="utf-8")

            import_start = source.index("import os")
            import_line, import_col = line_col_for(source, "import os")
            import_node = FakeNode(
                "import_statement",
                import_start,
                import_start + len("import os"),
                (import_line, import_col),
            )

            class_start = source.index("class A:")
            class_line, class_col = line_col_for(source, "class A:")
            class_name_start = source.index("A:")
            class_name = FakeNode(
                "identifier",
                class_name_start,
                class_name_start + 1,
                line_col_for(source, "A:"),
            )
            class_node = FakeNode(
                "class_definition",
                class_start,
                class_start + len("class A:"),
                (class_line, class_col),
                fields={"name": class_name},
            )

            fn_start = source.index("def f():")
            fn_line, fn_col = line_col_for(source, "def f():")
            fn_name_start = source.index("f():")
            fn_name = FakeNode(
                "identifier",
                fn_name_start,
                fn_name_start + 1,
                line_col_for(source, "f():"),
            )
            fn_node = FakeNode(
                "function_definition",
                fn_start,
                fn_start + len("def f():"),
                (fn_line, fn_col),
                fields={"name": fn_name},
            )

            root = FakeNode(
                "module",
                0,
                len(source),
                (0, 0),
                named_children=[import_node, class_node, fn_node],
            )
            parser = FakeParser(FakeTree(root))

            with mock.patch("lazyviewer.symbols._load_parser", return_value=(parser, None)), mock.patch(
                "lazyviewer.symbols.read_text", return_value=source
            ):
                out, error = symbols.collect_symbols(path, max_symbols=2)

        self.assertIsNone(error)
        self.assertEqual(len(out), 2)
        self.assertEqual([entry.name for entry in out], ["import os", "A"])


if __name__ == "__main__":
    unittest.main()
