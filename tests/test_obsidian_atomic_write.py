from __future__ import annotations

import ast
from pathlib import Path


PUBLISHER_MODULES = [
    Path("src/personal_context_node/obsidian_daily.py"),
    Path("src/personal_context_node/obsidian_memory.py"),
    Path("src/personal_context_node/obsidian_review.py"),
    Path("src/personal_context_node/obsidian_sessions.py"),
    Path("src/personal_context_node/speaker_review.py"),
]


TEXT_OUTPUT_MODULES = [
    Path("src/personal_context_node/archive.py"),
    Path("src/personal_context_node/memory_export.py"),
    Path("src/personal_context_node/pipeline.py"),
]


def test_obsidian_publishers_do_not_write_notes_directly() -> None:
    direct_writes: list[str] = []
    for module_path in PUBLISHER_MODULES:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write_text":
                direct_writes.append(f"{module_path}:{node.lineno}")

    assert direct_writes == []


def test_text_outputs_do_not_write_directly() -> None:
    direct_writes: list[str] = []
    for module_path in TEXT_OUTPUT_MODULES:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write_text":
                direct_writes.append(f"{module_path}:{node.lineno}")

    assert direct_writes == []
