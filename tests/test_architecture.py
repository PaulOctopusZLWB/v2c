from __future__ import annotations

import ast
from pathlib import Path


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_core_does_not_import_concrete_adapters() -> None:
    root = Path("src/personal_context_node/core")
    forbidden_prefixes = (
        "personal_context_node.adapters",
        "funasr",
        "openai",
        "pyannote",
        "faster_whisper",
    )

    offenders: list[str] = []
    for path in root.rglob("*.py"):
        for import_name in _imports_for(path):
            if import_name.startswith(forbidden_prefixes):
                offenders.append(f"{path}: {import_name}")

    assert offenders == []
