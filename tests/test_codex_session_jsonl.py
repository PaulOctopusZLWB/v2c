from __future__ import annotations

import json
from pathlib import Path

import pytest

from personal_context_node.codex_session_jsonl import parse_codex_session_jsonl


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_parse_codex_session_jsonl_extracts_visible_messages_and_tools(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-06-22T02:12:21.042Z",
                "type": "session_meta",
                "payload": {
                    "id": "thread_1",
                    "timestamp": "2026-06-22T02:11:53.245Z",
                    "cwd": "/repo",
                    "originator": "Codex Desktop",
                    "cli_version": "0.142.0-alpha.6",
                },
            },
            {
                "timestamp": "2026-06-22T02:12:21.049Z",
                "type": "turn_context",
                "payload": {"turn_id": "turn_1", "model": "gpt-5.5"},
            },
            {
                "timestamp": "2026-06-22T02:12:21.050Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "private durable instruction"}],
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
                "timestamp": "2026-06-22T02:12:23.178Z",
                "type": "response_item",
                "payload": {"type": "reasoning", "encrypted_content": "must-not-leak"},
            },
            {
                "timestamp": "2026-06-22T02:12:25.176Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": "{\"cmd\":\"pwd\"}",
                    "call_id": "call_pwd",
                },
            },
            {
                "timestamp": "2026-06-22T02:12:25.204Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_pwd",
                    "output": "/repo\n",
                },
            },
            {
                "timestamp": "2026-06-22T02:13:01.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "助手回答"}],
                },
            },
        ],
    )

    document = parse_codex_session_jsonl(path)

    assert document.session_id == "thread_1"
    assert document.source_type == "codex_jsonl"
    assert document.cwd == "/repo"
    assert document.model == "gpt-5.5"
    assert document.started_at == "2026-06-22T02:11:53.245Z"
    assert document.ended_at == "2026-06-22T02:13:01.000Z"
    assert [turn.role for turn in document.turns] == ["user", "assistant"]
    assert [turn.text for turn in document.turns] == ["用户问题", "助手回答"]
    assert len(document.tool_events) == 2
    assert document.tool_events[0].tool_name == "exec_command"
    assert document.tool_events[0].arguments == {"cmd": "pwd"}
    assert document.tool_events[1].output_text == "/repo\n"
    assert "must-not-leak" not in document.searchable_text
    assert "private durable instruction" not in document.searchable_text


def test_parse_codex_session_jsonl_requires_session_meta(tmp_path: Path) -> None:
    path = tmp_path / "missing-meta.jsonl"
    _write_jsonl(path, [{"timestamp": "2026-06-22T02:12:21.053Z", "type": "event_msg", "payload": {}}])

    with pytest.raises(ValueError, match="missing session_meta"):
        parse_codex_session_jsonl(path)
