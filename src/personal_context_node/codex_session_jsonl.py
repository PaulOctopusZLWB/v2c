from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from personal_context_node.agent_session_types import AgentSessionDocument, AgentToolEvent, AgentTurn


VISIBLE_MESSAGE_ROLES = {"user", "assistant"}


def parse_codex_session_jsonl(path: Path) -> AgentSessionDocument:
    rows = [_load_json_line(line, line_number=index + 1) for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()) if line.strip()]
    meta = _first_payload(rows, "session_meta")
    if meta is None:
        raise ValueError("missing session_meta")

    session_id = str(meta["id"])
    started_at = str(meta.get("timestamp") or rows[0].get("timestamp"))
    cwd = _optional_str(meta.get("cwd"))
    originator = _optional_str(meta.get("originator"))
    cli_version = _optional_str(meta.get("cli_version"))
    model: str | None = None
    turns: list[AgentTurn] = []
    tool_events: list[AgentToolEvent] = []
    ended_at: str | None = started_at

    for row in rows:
        timestamp = str(row.get("timestamp") or started_at)
        ended_at = timestamp
        row_type = row.get("type")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row_type == "turn_context":
            context_model = payload.get("model")
            if isinstance(context_model, str) and context_model:
                model = context_model
            if isinstance(payload.get("cwd"), str):
                cwd = str(payload["cwd"])
            continue
        if row_type != "response_item":
            continue
        item_type = payload.get("type")
        if item_type == "message":
            role = payload.get("role")
            if role not in VISIBLE_MESSAGE_ROLES:
                continue
            text = _content_text(payload.get("content"))
            if not text:
                continue
            turns.append(
                AgentTurn(
                    turn_index=len(turns) + 1,
                    role=str(role),
                    occurred_at=timestamp,
                    text=text,
                    metadata={"source": "response_item"},
                )
            )
        elif item_type == "function_call":
            tool_events.append(
                AgentToolEvent(
                    event_index=len(tool_events) + 1,
                    occurred_at=timestamp,
                    tool_name=str(payload.get("name") or "unknown"),
                    call_id=_optional_str(payload.get("call_id")),
                    arguments=_parse_arguments(payload.get("arguments")),
                    output_text=None,
                    status="called",
                )
            )
        elif item_type == "function_call_output":
            tool_events.append(
                AgentToolEvent(
                    event_index=len(tool_events) + 1,
                    occurred_at=timestamp,
                    tool_name="function_call_output",
                    call_id=_optional_str(payload.get("call_id")),
                    arguments={},
                    output_text=_optional_str(payload.get("output")),
                    status="completed",
                )
            )

    return AgentSessionDocument(
        session_id=session_id,
        source_type="codex_jsonl",
        source_path=str(path),
        source_sha256=_sha256(path),
        originator=originator,
        cli_version=cli_version,
        cwd=cwd,
        model=model,
        started_at=started_at,
        ended_at=ended_at,
        title=_title_from_turns(turns),
        turns=turns,
        tool_events=tool_events,
    )


def _load_json_line(line: str, *, line_number: int) -> dict[str, Any]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSONL at line {line_number}: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid JSONL at line {line_number}: expected object")
    return value


def _first_payload(rows: list[dict[str, Any]], row_type: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("type") == row_type and isinstance(row.get("payload"), dict):
            return row["payload"]
    return None


def _content_text(content: object) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def _parse_arguments(arguments: object) -> dict[str, object]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str) and arguments.strip():
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"raw": arguments}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _title_from_turns(turns: list[AgentTurn]) -> str | None:
    for turn in turns:
        if turn.role == "user":
            return turn.text[:80]
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
