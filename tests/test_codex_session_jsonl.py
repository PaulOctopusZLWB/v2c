from __future__ import annotations

from dataclasses import asdict
import hashlib
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
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-06-22T02:12:21.053Z",
                "type": "event_msg",
                "payload": {},
            }
        ],
    )

    with pytest.raises(ValueError, match="missing session_meta"):
        parse_codex_session_jsonl(path)


def test_parse_codex_session_jsonl_records_source_path_and_sha256(tmp_path: Path) -> None:
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
                },
            }
        ],
    )

    document = parse_codex_session_jsonl(path)

    assert document.source_path == str(path)
    assert document.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_parse_codex_session_jsonl_requires_session_meta_id(tmp_path: Path) -> None:
    path = tmp_path / "missing-id.jsonl"
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-06-22T02:12:21.042Z",
                "type": "session_meta",
                "payload": {"timestamp": "2026-06-22T02:11:53.245Z"},
            }
        ],
    )

    with pytest.raises(ValueError, match="missing session_meta.id"):
        parse_codex_session_jsonl(path)


def test_parse_codex_session_jsonl_requires_session_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "missing-timestamp.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "session_meta",
                "payload": {"id": "thread_1"},
            }
        ],
    )

    with pytest.raises(ValueError, match="missing session timestamp"):
        parse_codex_session_jsonl(path)


def test_parse_codex_session_jsonl_skips_invalid_final_line_without_leaking_raw_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truncated-tail.jsonl"
    raw_tail = (
        '{"timestamp":"2026-06-22T02:12:22.000Z","type":"response_item",'
        '"payload":{"type":"message","role":"assistant","content":[{"text":"TAIL_SECRET"}]}'
    )
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-22T02:12:21.042Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "thread_1",
                            "timestamp": "2026-06-22T02:11:53.245Z",
                        },
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-06-22T02:12:21.053Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "visible"}],
                        },
                    },
                    sort_keys=True,
                ),
                raw_tail,
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    document = parse_codex_session_jsonl(path)

    assert [turn.text for turn in document.turns] == ["visible"]
    assert "TAIL_SECRET" not in document.searchable_text
    assert raw_tail not in document.searchable_text


def test_parse_codex_session_jsonl_rejects_invalid_non_final_line_without_raw_content(
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid-middle.jsonl"
    raw_middle = '{"secret":"MIDDLE_SECRET"'
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-06-22T02:12:21.042Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "thread_1",
                            "timestamp": "2026-06-22T02:11:53.245Z",
                        },
                    },
                    sort_keys=True,
                ),
                raw_middle,
                json.dumps(
                    {
                        "timestamp": "2026-06-22T02:12:21.053Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "visible"}],
                        },
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid JSONL at line 2") as exc_info:
        parse_codex_session_jsonl(path)

    assert raw_middle not in str(exc_info.value)
    assert "MIDDLE_SECRET" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_parse_codex_session_jsonl_preserves_invalid_function_call_arguments(
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid-arguments.jsonl"
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-06-22T02:12:21.042Z",
                "type": "session_meta",
                "payload": {
                    "id": "thread_1",
                    "timestamp": "2026-06-22T02:11:53.245Z",
                },
            },
            {
                "timestamp": "2026-06-22T02:12:25.176Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": "{not-json",
                    "call_id": "call_bad",
                },
            },
        ],
    )

    document = parse_codex_session_jsonl(path)

    assert document.tool_events[0].arguments == {"raw": "{not-json"}


def test_parse_codex_session_jsonl_sanitizes_non_string_scalar_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "non-string-scalars.jsonl"
    started_at = "2026-06-22T02:11:53.245Z"
    _write_jsonl(
        path,
        [
            {
                "timestamp": started_at,
                "type": "session_meta",
                "payload": {
                    "id": "thread_1",
                    "timestamp": started_at,
                },
            },
            {
                "timestamp": {"secret": "TIMESTAMP_SECRET"},
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "visible"}],
                },
            },
            {
                "timestamp": ["TOOL_TIMESTAMP_SECRET"],
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": {"secret": "TOOL_NAME_SECRET"},
                    "arguments": "{}",
                    "call_id": "call_bad_scalars",
                },
            },
        ],
    )

    document = parse_codex_session_jsonl(path)

    assert document.turns[0].occurred_at == started_at
    assert document.tool_events[0].occurred_at == started_at
    assert document.tool_events[0].tool_name == "unknown"
    assert document.ended_at == started_at
    document_metadata = json.dumps(asdict(document), ensure_ascii=False, sort_keys=True)
    for secret in ("TIMESTAMP_SECRET", "TOOL_TIMESTAMP_SECRET", "TOOL_NAME_SECRET"):
        assert secret not in document_metadata
        assert secret not in document.searchable_text


def test_parse_codex_session_jsonl_ignores_malformed_message_roles(
    tmp_path: Path,
) -> None:
    path = tmp_path / "malformed-roles.jsonl"
    _write_jsonl(
        path,
        [
            {
                "timestamp": "2026-06-22T02:12:21.042Z",
                "type": "session_meta",
                "payload": {
                    "id": "thread_1",
                    "timestamp": "2026-06-22T02:11:53.245Z",
                },
            },
            {
                "timestamp": "2026-06-22T02:12:22.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": {"secret": "ROLE_DICT_SECRET"},
                    "content": [{"type": "input_text", "text": "dict role text"}],
                },
            },
            {
                "timestamp": "2026-06-22T02:12:23.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": ["ROLE_LIST_SECRET"],
                    "content": [{"type": "input_text", "text": "list role text"}],
                },
            },
            {
                "timestamp": "2026-06-22T02:12:24.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "visible"}],
                },
            },
        ],
    )

    document = parse_codex_session_jsonl(path)

    assert [turn.text for turn in document.turns] == ["visible"]
    document_metadata = json.dumps(asdict(document), ensure_ascii=False, sort_keys=True)
    for secret in ("ROLE_DICT_SECRET", "ROLE_LIST_SECRET"):
        assert secret not in document.searchable_text
        assert secret not in document_metadata


def test_parse_codex_session_jsonl_does_not_move_ended_at_back_for_bad_timestamp(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bad-ended-at.jsonl"
    started_at = "2026-06-22T02:11:53.245Z"
    later_at = "2026-06-22T02:13:01.000Z"
    _write_jsonl(
        path,
        [
            {
                "timestamp": started_at,
                "type": "session_meta",
                "payload": {
                    "id": "thread_1",
                    "timestamp": started_at,
                },
            },
            {
                "timestamp": later_at,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "latest visible"}],
                },
            },
            {
                "timestamp": {"secret": "BAD_ENDED_AT_SECRET"},
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": "{}",
                    "call_id": "call_bad_timestamp",
                },
            },
        ],
    )

    document = parse_codex_session_jsonl(path)

    assert document.ended_at == later_at
    assert document.tool_events[0].occurred_at == started_at
    document_metadata = json.dumps(asdict(document), ensure_ascii=False, sort_keys=True)
    assert "BAD_ENDED_AT_SECRET" not in document.searchable_text
    assert "BAD_ENDED_AT_SECRET" not in document_metadata
