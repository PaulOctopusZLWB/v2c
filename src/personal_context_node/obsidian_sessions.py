from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class PublishSessionNotesResult:
    notes_written: int


def publish_session_notes(*, config: AppConfig, day: str, source_run_id: str | None = None) -> PublishSessionNotesResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        sessions = fetch_all(
            conn,
            """
            select
              s.session_id, s.date_key, s.started_at, s.ended_at, s.segment_count, s.active_speech_ms,
              sm.content_json as summary_json
            from sessions s
            left join summaries sm
              on sm.summary_type = 'session'
             and sm.target_type = 'session'
             and sm.target_id = s.session_id
             and sm.prompt_version = 'llm_port.session_summary.v1'
            where s.date_key = ?
            order by s.started_at
            """,
            (day,),
        )
    finally:
        conn.close()

    output_dir = config.obsidian_vault / "20_Conversations" / day
    output_dir.mkdir(parents=True, exist_ok=True)
    for session in sessions:
        note_path = output_dir / f"{session['session_id']}.md"
        existing_text = note_path.read_text(encoding="utf-8") if note_path.exists() else None
        write_text_atomic(note_path, _session_note_text(session, existing_text=existing_text, source_run_id=source_run_id))
    return PublishSessionNotesResult(notes_written=len(sessions))


def _session_note_text(session: dict[str, object], *, existing_text: str | None = None, source_run_id: str | None = None) -> str:
    session_id = str(session["session_id"])
    summary_json = session.get("summary_json")
    summary = json.loads(str(summary_json)) if summary_json else None
    title = summary["headline"] if summary else f"Session {session_id}"
    managed_lines = _summary_lines(session, summary)
    user_notes = _existing_user_notes(existing_text)
    return "\n".join(
        [
            "---",
            "pcn_schema: markdown_note.v1",
            "note_type: session",
            f"date_key: {session['date_key']}",
            f"session_id: {session_id}",
            "generated_by: personal-context-node",
            f"generated_at: {datetime.now(timezone.utc).isoformat()}",
            *([f"source_run_id: {source_run_id}"] if source_run_id else []),
            "pcn_managed: true",
            "---",
            "",
            f"# {title}",
            "",
            _block_start("session_summary", "managed"),
            *managed_lines,
            _block_end("session_summary"),
            "",
            "## User Notes",
            "",
            _block_start("user_notes", "user"),
            user_notes,
            _block_end("user_notes"),
        ]
    )


def _block_start(block_id: str, kind: str) -> str:
    return f'<!-- pcn:block start id="{block_id}" kind="{kind}" version="1" -->'


def _block_end(block_id: str) -> str:
    return f'<!-- pcn:block end id="{block_id}" -->'


def _existing_user_notes(existing_text: str | None) -> str:
    if not existing_text:
        return ""
    patterns = [
        r'<!-- pcn:block start id="user_notes" kind="user" version="1" -->\n?(.*?)\n?<!-- pcn:block end id="user_notes" -->',
        r'<!-- pcn:user start type="user_notes" -->\n?(.*?)\n?<!-- pcn:user end type="user_notes" -->',
    ]
    for pattern in patterns:
        match = re.search(pattern, existing_text, flags=re.DOTALL)
        if match:
            return match.group(1).rstrip("\n")
    return ""


def _summary_lines(session: dict[str, object], summary: dict[str, object] | None) -> list[str]:
    metadata = [
        f"started_at: {session['started_at']}",
        f"ended_at: {session['ended_at']}",
        f"segment_count: {session['segment_count']}",
        f"active_speech_ms: {session['active_speech_ms']}",
        "",
    ]
    if summary is None:
        return [
            *metadata,
            "完整转写不进入 session note；需要时从 SQLite transcript_segments 查询。",
        ]
    lines = [
        *metadata,
        f"## {summary['headline']}",
        "",
        str(summary["summary"]),
        "",
    ]
    lines.extend(_item_lines("Decision", summary.get("decisions", [])))
    lines.extend(_todo_lines(summary.get("todos", [])))
    lines.extend(_plain_lines("Open Question", summary.get("open_questions", [])))
    lines.extend(["", "完整转写不进入 session note；需要时从 SQLite transcript_segments 查询。"])
    return lines


def _item_lines(label: str, items: object) -> list[str]:
    lines: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            lines.append(f"- {label}: {item['text']}")
    if lines:
        lines.append("")
    return lines


def _todo_lines(items: object) -> list[str]:
    lines: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            lines.append(f"- Todo: {item['text']} (owner: {item['owner']})")
    if lines:
        lines.append("")
    return lines


def _plain_lines(label: str, items: object) -> list[str]:
    lines = [f"- {label}: {item}" for item in items if isinstance(item, str)] if isinstance(items, list) else []
    if lines:
        lines.append("")
    return lines
