from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from personal_context_node.agent_session_types import AgentSessionDocument
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class AgentSessionImportResult:
    agent_session_id: str
    sessions_imported: int
    turns_imported: int
    tool_events_imported: int
    evidence_refs_created: int


def import_agent_session(*, config: AppConfig, document: AgentSessionDocument) -> AgentSessionImportResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        existing = conn.execute(
            "select agent_session_id from agent_sessions where agent_session_id = ? and source_type = ?",
            (document.session_id, document.source_type),
        ).fetchone()
        if existing is not None:
            return AgentSessionImportResult(
                agent_session_id=document.session_id,
                sessions_imported=0,
                turns_imported=0,
                tool_events_imported=0,
                evidence_refs_created=0,
            )
        now = _now()
        conn.execute(
            """
            insert into agent_sessions (
              agent_session_id, source_type, source_path, source_sha256,
              originator, cli_version, cwd, model, started_at, ended_at, title,
              message_count, tool_event_count, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.session_id,
                document.source_type,
                document.source_path,
                document.source_sha256,
                document.originator,
                document.cli_version,
                document.cwd,
                document.model,
                document.started_at,
                document.ended_at,
                document.title,
                len(document.turns),
                len(document.tool_events),
                now,
                now,
            ),
        )
        for turn in document.turns:
            turn_id = _turn_id(document.session_id, turn.turn_index)
            conn.execute(
                """
                insert into agent_turns (
                  agent_turn_id, agent_session_id, turn_index, role,
                  occurred_at, text, metadata_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    document.session_id,
                    turn.turn_index,
                    turn.role,
                    turn.occurred_at,
                    turn.text,
                    json.dumps(turn.metadata, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            _insert_turn_evidence_ref(
                conn,
                document=document,
                turn_id=turn_id,
                turn_index=turn.turn_index,
                quote=turn.text,
                now=now,
            )
        for event in document.tool_events:
            conn.execute(
                """
                insert into agent_tool_events (
                  agent_tool_event_id, agent_session_id, event_index, occurred_at,
                  tool_name, call_id, arguments_json, output_text, status, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _tool_event_id(document.session_id, event.event_index),
                    document.session_id,
                    event.event_index,
                    event.occurred_at,
                    event.tool_name,
                    event.call_id,
                    json.dumps(event.arguments, ensure_ascii=False, sort_keys=True),
                    event.output_text,
                    event.status,
                    now,
                ),
            )
        conn.commit()
        return AgentSessionImportResult(
            agent_session_id=document.session_id,
            sessions_imported=1,
            turns_imported=len(document.turns),
            tool_events_imported=len(document.tool_events),
            evidence_refs_created=len(document.turns),
        )
    finally:
        conn.close()


def render_agent_session_markdown(*, config: AppConfig, agent_session_id: str) -> str:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        session_rows = fetch_all(conn, "select * from agent_sessions where agent_session_id = ?", (agent_session_id,))
        if not session_rows:
            raise ValueError(f"unknown agent session: {agent_session_id}")
        session = session_rows[0]
        turns = fetch_all(
            conn,
            "select role, occurred_at, text from agent_turns where agent_session_id = ? order by turn_index",
            (agent_session_id,),
        )
        tools = fetch_all(
            conn,
            """
            select event_index, occurred_at, tool_name, call_id, arguments_json, output_text, status
            from agent_tool_events
            where agent_session_id = ?
            order by event_index
            """,
            (agent_session_id,),
        )
    finally:
        conn.close()
    return _markdown(session=session, turns=turns, tools=tools)


def _insert_turn_evidence_ref(
    conn: sqlite3.Connection,
    *,
    document: AgentSessionDocument,
    turn_id: str,
    turn_index: int,
    quote: str,
    now: str,
) -> None:
    conn.execute(
        """
        insert or ignore into evidence_refs (
          evidence_id, source_type, source_ref, source_id, owner_id, quote, summary, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"ev_{turn_id}",
            "agent_session_turn",
            f"{document.source_type}:{document.session_id}:turn:{turn_index}",
            turn_id,
            None,
            quote[:1000],
            None,
            now,
        ),
    )


def _markdown(*, session: dict[str, object], turns: list[dict[str, object]], tools: list[dict[str, object]]) -> str:
    title = f"Codex Session {session['agent_session_id']}"
    lines = [
        f"# {title}",
        "",
        "---",
        "note_type: agent_session",
        f"agent_session_id: {session['agent_session_id']}",
        f"source_type: {session['source_type']}",
        f"started_at: {session['started_at']}",
        f"cwd: {session.get('cwd') or ''}",
        f"model: {session.get('model') or ''}",
        "---",
        "",
        "## Turns",
        "",
    ]
    for turn in turns:
        lines.append(f"- `{turn['occurred_at']}` **{turn['role']}**: {turn['text']}")
    lines.extend(["", "## Tool Events", ""])
    for tool in tools:
        arguments = json.loads(str(tool["arguments_json"]))
        lines.append(
            f"- `{tool['occurred_at']}` `{tool['tool_name']}` status={tool['status']} call_id={tool.get('call_id') or ''}"
        )
        if arguments:
            lines.append(f"  - arguments: `{json.dumps(arguments, ensure_ascii=False, sort_keys=True)}`")
        if tool.get("output_text"):
            lines.append(f"  - output: `{str(tool['output_text']).strip()}`")
    lines.append("")
    return "\n".join(lines)


def _turn_id(session_id: str, turn_index: int) -> str:
    return f"{session_id}:turn:{turn_index}"


def _tool_event_id(session_id: str, event_index: int) -> str:
    return f"{session_id}:tool:{event_index}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
