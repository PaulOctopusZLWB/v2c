from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.storage.sqlite import connect, fetch_all


THREAD_ID = "019eed19-371d-7e41-87e9-0e17785a8a25"


def _write_codex_jsonl(path: Path) -> None:
    rows = [
        {
            "timestamp": "2026-06-22T02:12:21.042Z",
            "type": "session_meta",
            "payload": {
                "id": THREAD_ID,
                "timestamp": "2026-06-22T02:11:53.245Z",
                "cwd": "/Users/paul/Documents/v2c 本地部署",
                "originator": "Codex Desktop",
                "cli_version": "0.142.0-alpha.6",
                "model_provider": "openai",
            },
        },
        {
            "timestamp": "2026-06-22T02:12:21.049Z",
            "type": "turn_context",
            "payload": {
                "turn_id": "turn_1",
                "cwd": "/Users/paul/Documents/v2c 本地部署",
                "model": "gpt-5.5",
            },
        },
        {
            "timestamp": "2026-06-22T02:12:21.053Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "评估把 Codex 本地会话导入 PCN 做会话系统整理。",
                    }
                ],
            },
        },
        {
            "timestamp": "2026-06-22T02:12:23.178Z",
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "encrypted_content": "opaque-private-field",
            },
        },
        {
            "timestamp": "2026-06-22T02:12:25.176Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": "{\"cmd\":\"rg --files\"}",
                "call_id": "call_files",
            },
        },
        {
            "timestamp": "2026-06-22T02:12:25.204Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_files",
                "output": "src/personal_context_node/cli.py\n",
            },
        },
        {
            "timestamp": "2026-06-22T02:13:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "建议 PCN 作为 source of truth，Codex JSONL 只作为导入源。",
                    }
                ],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_codex_jsonl_import_show_and_publish_e2e(tmp_path: Path) -> None:
    source = tmp_path / "codex-session.jsonl"
    _write_codex_jsonl(source)
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    runner = CliRunner()

    import_result = runner.invoke(
        app,
        [
            "agent",
            "import-codex",
            "--jsonl",
            str(source),
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
        ],
    )

    assert import_result.exit_code == 0, import_result.output
    assert f"agent_session_id={THREAD_ID}" in import_result.output
    assert "turns_imported=2" in import_result.output
    assert "tool_events_imported=2" in import_result.output
    assert "evidence_refs_created=2" in import_result.output

    conn = connect(data / "db" / "personal_context.sqlite")
    try:
        sessions = fetch_all(
            conn,
            "select agent_session_id, source_type, cwd, model, message_count, tool_event_count from agent_sessions",
        )
        turns = fetch_all(
            conn,
            "select role, text from agent_turns order by turn_index",
        )
        evidence_refs = fetch_all(
            conn,
            "select source_type, source_id, quote from evidence_refs order by source_id",
        )
    finally:
        conn.close()

    assert sessions == [
        {
            "agent_session_id": THREAD_ID,
            "source_type": "codex_jsonl",
            "cwd": "/Users/paul/Documents/v2c 本地部署",
            "model": "gpt-5.5",
            "message_count": 2,
            "tool_event_count": 2,
        }
    ]
    assert turns == [
        {
            "role": "user",
            "text": "评估把 Codex 本地会话导入 PCN 做会话系统整理。",
        },
        {
            "role": "assistant",
            "text": "建议 PCN 作为 source of truth，Codex JSONL 只作为导入源。",
        },
    ]
    assert evidence_refs == [
        {
            "source_type": "agent_session_turn",
            "source_id": f"{THREAD_ID}:turn:1",
            "quote": "评估把 Codex 本地会话导入 PCN 做会话系统整理。",
        },
        {
            "source_type": "agent_session_turn",
            "source_id": f"{THREAD_ID}:turn:2",
            "quote": "建议 PCN 作为 source of truth，Codex JSONL 只作为导入源。",
        },
    ]

    show_result = runner.invoke(
        app,
        [
            "agent",
            "show",
            "--session-id",
            THREAD_ID,
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
        ],
    )

    assert show_result.exit_code == 0, show_result.output
    assert "# Codex Session 019eed19-371d-7e41-87e9-0e17785a8a25" in show_result.output
    assert "## Turns" in show_result.output
    assert "## Tool Events" in show_result.output
    assert "opaque-private-field" not in show_result.output

    publish_result = runner.invoke(
        app,
        [
            "agent",
            "publish",
            "--session-id",
            THREAD_ID,
            "--data-dir",
            str(data),
            "--obsidian-vault",
            str(vault),
        ],
    )

    assert publish_result.exit_code == 0, publish_result.output
    note_path = vault / "40_Agent_Sessions" / "2026-06-22" / f"{THREAD_ID}.md"
    assert f"note_path={note_path}" in publish_result.output
    note_text = note_path.read_text(encoding="utf-8")
    assert "note_type: agent_session" in note_text
    assert "source_type: codex_jsonl" in note_text
    assert "建议 PCN 作为 source of truth" in note_text
    assert "opaque-private-field" not in note_text
