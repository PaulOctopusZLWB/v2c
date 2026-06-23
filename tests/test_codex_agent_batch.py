from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.codex_agent_batch import import_codex_batch
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all


def _write_codex_jsonl(
    path: Path,
    *,
    thread_id: str,
    started_at: str = "2026-06-22T02:11:53.245Z",
    cwd: str = "/repo",
    user_text: str = "用户问题",
    assistant_text: str = "助手回答",
) -> None:
    rows = [
        {
            "timestamp": started_at,
            "type": "session_meta",
            "payload": {
                "id": thread_id,
                "timestamp": started_at,
                "cwd": cwd,
                "originator": "Codex Desktop",
                "cli_version": "0.142.0-alpha.6",
            },
        },
        {
            "timestamp": started_at,
            "type": "turn_context",
            "payload": {"turn_id": "turn_1", "cwd": cwd, "model": "gpt-5.5"},
        },
        {
            "timestamp": "2026-06-22T02:12:21.053Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_text}],
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


def _session_ids(config: AppConfig) -> list[str]:
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select agent_session_id from agent_sessions order by agent_session_id")
    finally:
        conn.close()
    return [str(row["agent_session_id"]) for row in rows]


def test_import_codex_batch_imports_directory_and_repeated_run_skips_existing(tmp_path: Path) -> None:
    source_dir = tmp_path / "jsonl"
    source_dir.mkdir()
    _write_codex_jsonl(source_dir / "b.jsonl", thread_id="thread_b", user_text="第二个问题")
    _write_codex_jsonl(source_dir / "a.jsonl", thread_id="thread_a", user_text="第一个问题")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "pcn-vault")

    first = import_codex_batch(config=config, jsonl_dirs=[source_dir], jsonl_paths=[])
    second = import_codex_batch(config=config, jsonl_dirs=[source_dir], jsonl_paths=[])

    assert first.files_found == 2
    assert first.sessions_imported == 2
    assert first.sessions_skipped == 0
    assert first.turns_imported == 4
    assert first.index_days == ["2026-06-22"]
    assert second.files_found == 2
    assert second.sessions_imported == 0
    assert second.sessions_skipped == 2
    assert second.index_days == ["2026-06-22"]
    assert _session_ids(config) == ["thread_a", "thread_b"]


def test_import_codex_batch_rejects_conflict_before_writing_any_new_sessions(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "pcn-vault")
    existing = tmp_path / "existing.jsonl"
    _write_codex_jsonl(existing, thread_id="thread_1", user_text="原始问题")
    import_codex_batch(config=config, jsonl_paths=[existing], jsonl_dirs=[])
    new_session = tmp_path / "new.jsonl"
    conflict = tmp_path / "conflict-copy.jsonl"
    _write_codex_jsonl(new_session, thread_id="thread_2", user_text="不应该被写入")
    _write_codex_jsonl(conflict, thread_id="thread_1", user_text="同 id 不同 source path")

    with pytest.raises(ValueError, match="source identity differs"):
        import_codex_batch(config=config, jsonl_paths=[new_session, conflict], jsonl_dirs=[])

    assert _session_ids(config) == ["thread_1"]


def test_import_codex_batch_rejects_invalid_jsonl_before_writing_any_sessions(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "pcn-vault")
    valid = tmp_path / "valid.jsonl"
    invalid = tmp_path / "invalid.jsonl"
    _write_codex_jsonl(valid, thread_id="thread_valid")
    invalid.write_text('{"type":"response_item","payload":{"type":"message"}}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="missing session_meta"):
        import_codex_batch(config=config, jsonl_paths=[valid, invalid], jsonl_dirs=[])

    assert not config.database_path.exists()


def test_agent_import_codex_batch_cli_writes_supcon_index(tmp_path: Path) -> None:
    source_dir = tmp_path / "jsonl"
    source_dir.mkdir()
    _write_codex_jsonl(
        source_dir / "session.jsonl",
        thread_id="thread_cli_batch",
        cwd="/Users/paul/Documents/v2c",
        user_text="把 Codex 工作会话导入 PCN。",
    )
    data_dir = tmp_path / "data"
    pcn_vault = tmp_path / "PersonalContext"
    supcon_vault = tmp_path / "Supcon"
    (supcon_vault / ".obsidian").mkdir(parents=True)

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "import-codex-batch",
            "--jsonl-dir",
            str(source_dir),
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(pcn_vault),
            "--supcon-vault",
            str(supcon_vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "files_found=1" in result.output
    assert "sessions_imported=1" in result.output
    assert "sessions_skipped=0" in result.output
    assert "index_notes_written=1" in result.output
    note_path = supcon_vault / "Codex 工作会话" / "2026-06-22.md"
    note_text = note_path.read_text(encoding="utf-8")
    assert "thread_cli_batch" in note_text
    assert "source_sha256:" in note_text
    assert "thread_cli_batch:turn:1" in note_text
    assert "把 Codex 工作会话导入 PCN。" in note_text


def test_agent_import_codex_batch_cli_no_index_skips_supcon_write(tmp_path: Path) -> None:
    source_dir = tmp_path / "jsonl"
    source_dir.mkdir()
    _write_codex_jsonl(source_dir / "session.jsonl", thread_id="thread_no_index")
    supcon_vault = tmp_path / "Supcon"
    (supcon_vault / ".obsidian").mkdir(parents=True)

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "import-codex-batch",
            "--jsonl-dir",
            str(source_dir),
            "--data-dir",
            str(tmp_path / "data"),
            "--obsidian-vault",
            str(tmp_path / "PersonalContext"),
            "--supcon-vault",
            str(supcon_vault),
            "--no-index",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "index_notes_written=0" in result.output
    assert not (supcon_vault / "Codex 工作会话").exists()


def test_agent_import_codex_batch_cli_accepts_repeated_jsonl_paths(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    _write_codex_jsonl(first, thread_id="thread_first")
    _write_codex_jsonl(second, thread_id="thread_second")

    result = CliRunner().invoke(
        app,
        [
            "agent",
            "import-codex-batch",
            "--jsonl",
            str(first),
            "--jsonl",
            str(second),
            "--data-dir",
            str(tmp_path / "data"),
            "--obsidian-vault",
            str(tmp_path / "PersonalContext"),
            "--no-index",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "files_found=2" in result.output
    assert "sessions_imported=2" in result.output
    assert "index_notes_written=0" in result.output
