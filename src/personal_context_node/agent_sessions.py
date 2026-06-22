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
        try:
            existing = conn.execute(
                """
                select agent_session_id, source_type, source_path, source_sha256
                from agent_sessions
                where agent_session_id = ?
                """,
                (document.session_id,),
            ).fetchone()
            if existing is not None:
                _ensure_matching_source_identity(existing, document)
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
            evidence_refs_created = 0
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
                evidence_refs_created += _insert_turn_evidence_ref(
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
                evidence_refs_created=evidence_refs_created,
            )
        except Exception:
            conn.rollback()
            raise
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
) -> int:
    expected = {
        "evidence_id": f"ev_{turn_id}",
        "source_type": "agent_session_turn",
        "source_ref": f"{document.source_type}:{document.session_id}:turn:{turn_index}",
        "source_id": turn_id,
        "owner_id": None,
        "quote": quote[:1000],
        "summary": None,
    }
    existing_refs = conn.execute(
        """
        select evidence_id, source_type, source_ref, source_id, owner_id, quote, summary
        from evidence_refs
        where evidence_id = ? or (source_type = ? and source_ref = ?)
        """,
        (expected["evidence_id"], expected["source_type"], expected["source_ref"]),
    ).fetchall()
    for existing in existing_refs:
        if _evidence_ref_matches(existing, expected):
            return 0
        conflict_key = "evidence_id"
        if existing["source_type"] == expected["source_type"] and existing["source_ref"] == expected["source_ref"]:
            conflict_key = "source_ref"
        raise ValueError(
            f"conflicting evidence ref {conflict_key} for agent turn {turn_id}: "
            f"{conflict_key} already exists"
        )
    conn.execute(
        """
        insert into evidence_refs (
          evidence_id, source_type, source_ref, source_id, owner_id, quote, summary, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            expected["evidence_id"],
            expected["source_type"],
            expected["source_ref"],
            expected["source_id"],
            expected["owner_id"],
            expected["quote"],
            expected["summary"],
            now,
        ),
    )
    return 1


def _markdown(*, session: dict[str, object], turns: list[dict[str, object]], tools: list[dict[str, object]]) -> str:
    title = f"Codex Session {session['agent_session_id']}"
    lines = [
        "---",
        f"note_type: {_frontmatter_scalar('agent_session')}",
        f"agent_session_id: {_frontmatter_scalar(session['agent_session_id'])}",
        f"source_type: {_frontmatter_scalar(session['source_type'])}",
        f"started_at: {_frontmatter_scalar(session['started_at'])}",
        f"cwd: {_frontmatter_scalar(session.get('cwd') or '')}",
        f"model: {_frontmatter_scalar(session.get('model') or '')}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    _append_visible_title(lines, session.get("title"))
    lines.extend(["## Turns", ""])
    for turn in turns:
        turn_text = str(turn["text"])
        prefix = f"- `{turn['occurred_at']}` **{turn['role']}**"
        if _is_simple_inline_markdown(turn_text):
            lines.append(f"{prefix}: {turn_text}")
        else:
            lines.append(f"{prefix}:")
            _append_fenced_block(lines, turn_text, indent="  ")
    lines.extend(["", "## Tool Events", ""])
    for tool in tools:
        arguments_raw = str(tool["arguments_json"])
        lines.append(
            f"- `{tool['occurred_at']}` `{tool['tool_name']}` status={tool['status']} call_id={tool.get('call_id') or ''}"
        )
        try:
            arguments = json.loads(arguments_raw)
        except json.JSONDecodeError:
            lines.append("  - arguments:")
            _append_fenced_block(lines, arguments_raw, indent="    ")
            arguments = None
        if arguments:
            arguments_json = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
            if _is_simple_inline_markdown(arguments_json):
                lines.append(f"  - arguments: `{arguments_json}`")
            else:
                lines.append("  - arguments:")
                _append_fenced_block(lines, arguments_json, indent="    ")
        if tool.get("output_text"):
            output_text = str(tool["output_text"])
            if _is_simple_inline_markdown(output_text):
                lines.append(f"  - output: `{output_text}`")
            else:
                lines.append("  - output:")
                _append_fenced_block(lines, output_text, indent="    ")
    lines.append("")
    return "\n".join(lines)


def _ensure_matching_source_identity(row: sqlite3.Row, document: AgentSessionDocument) -> None:
    if (
        row["source_type"] == document.source_type
        and row["source_path"] == document.source_path
        and row["source_sha256"] == document.source_sha256
    ):
        return
    raise ValueError(
        f"agent session source identity differs for {document.session_id}: "
        f"existing source_type={row['source_type']!r}, source_path={row['source_path']!r}, "
        f"source_sha256={row['source_sha256']!r}; incoming source_type={document.source_type!r}, "
        f"source_path={document.source_path!r}, source_sha256={document.source_sha256!r}"
    )


def _evidence_ref_matches(row: sqlite3.Row, expected: dict[str, object]) -> bool:
    return all(row[key] == value for key, value in expected.items())


def _append_visible_title(lines: list[str], title: object) -> None:
    if title is None or title == "":
        return
    title_text = str(title)
    if _is_simple_inline_markdown(title_text):
        lines.extend([f"**Title**: {title_text}", ""])
        return
    lines.extend(["## Title", ""])
    _append_fenced_block(lines, title_text)
    lines.append("")


def _is_simple_inline_markdown(text: str) -> bool:
    return "\n" not in text and "\r" not in text and "`" not in text


def _frontmatter_scalar(value: object) -> str:
    text = str(value)
    if _is_safe_plain_frontmatter_scalar(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _is_safe_plain_frontmatter_scalar(text: str) -> bool:
    if text == "":
        return False
    if "\n" in text or "\r" in text:
        return False
    if text.strip() != text:
        return False
    if text in {"---", "..."}:
        return False
    if ": " in text or " #" in text:
        return False
    return True


def _append_fenced_block(lines: list[str], text: str, *, indent: str = "") -> None:
    fence = _fence_for(text)
    lines.append(f"{indent}{fence}")
    text_lines = text.split("\n")
    for line in text_lines:
        lines.append(f"{indent}{line}")
    lines.append(f"{indent}{fence}")


def _fence_for(text: str) -> str:
    return "`" * max(3, _longest_backtick_run(text) + 1)


def _longest_backtick_run(text: str) -> int:
    longest = 0
    current = 0
    for char in text:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _turn_id(session_id: str, turn_index: int) -> str:
    return f"{session_id}:turn:{turn_index}"


def _tool_event_id(session_id: str, event_index: int) -> str:
    return f"{session_id}:tool:{event_index}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
