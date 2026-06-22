from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app


THREAD_ID = "thread_cli_1"


def _write_codex_jsonl(path: Path, *, assistant_text: str = "助手回答") -> None:
    rows = [
        {
            "timestamp": "2026-06-22T02:12:21.042Z",
            "type": "session_meta",
            "payload": {
                "id": THREAD_ID,
                "timestamp": "2026-06-22T02:11:53.245Z",
                "cwd": "/repo",
                "originator": "Codex Desktop",
                "cli_version": "0.142.0-alpha.6",
            },
        },
        {
            "timestamp": "2026-06-22T02:12:21.049Z",
            "type": "turn_context",
            "payload": {
                "turn_id": "turn_1",
                "cwd": "/repo",
                "model": "gpt-5.5",
            },
        },
        {
            "timestamp": "2026-06-22T02:12:21.053Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "用户问题"}],
            },
        },
        {
            "timestamp": "2026-06-22T02:13:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": assistant_text}],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_agent_import_codex_and_show_success_path(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    _write_codex_jsonl(source)
    runner = CliRunner()

    import_result = runner.invoke(
        app,
        [
            "agent",
            "import-codex",
            "--jsonl",
            str(source),
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
        ],
    )

    assert import_result.exit_code == 0, import_result.output
    assert f"agent_session_id={THREAD_ID}" in import_result.output
    assert "sessions_imported=1" in import_result.output
    assert "turns_imported=2" in import_result.output

    show_result = runner.invoke(
        app,
        [
            "agent",
            "show",
            "--session-id",
            THREAD_ID,
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
        ],
    )

    assert show_result.exit_code == 0, show_result.output
    assert f"# Codex Session {THREAD_ID}" in show_result.output
    assert "**user**: 用户问题" in show_result.output
    assert "**assistant**: 助手回答" in show_result.output


def test_agent_import_codex_maps_parser_value_error_to_cli_error(tmp_path: Path) -> None:
    source = tmp_path / "bad-session.jsonl"
    source.write_text('{"type":"response_item","payload":{"type":"message"}}\n', encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "import-codex",
            "--jsonl",
            str(source),
            "--data-dir",
            str(tmp_path / "data"),
            "--obsidian-vault",
            str(tmp_path / "vault"),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "missing session_meta" in result.output


def test_agent_import_codex_maps_storage_conflict_to_runtime_error(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    _write_codex_jsonl(source)
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "agent",
            "import-codex",
            "--jsonl",
            str(source),
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
        ],
    )
    assert first.exit_code == 0, first.output
    _write_codex_jsonl(source, assistant_text="changed source")

    second = runner.invoke(
        app,
        [
            "agent",
            "import-codex",
            "--jsonl",
            str(source),
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
        ],
    )

    assert second.exit_code == 1, second.output
    assert "source identity differs" in second.output
    assert "Invalid value" not in second.output


def test_agent_show_maps_unknown_session_to_cli_error(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    _write_codex_jsonl(source)
    setup_result = CliRunner().invoke(
        app,
        [
            "agent",
            "import-codex",
            "--jsonl",
            str(source),
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
        ],
    )
    assert setup_result.exit_code == 0, setup_result.output

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "show",
            "--session-id",
            "missing",
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "unknown agent session: missing" in result.output


def test_agent_show_missing_store_does_not_create_database(tmp_path: Path) -> None:
    data_dir = tmp_path / "missing-data"

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "show",
            "--session-id",
            "missing",
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(tmp_path / "vault"),
        ],
    )

    assert result.exit_code == 2, result.output
    assert "agent session store not found" in result.output
    assert not (data_dir / "db" / "personal_context.sqlite").exists()
