from __future__ import annotations

import json
from dataclasses import dataclass

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class PublishSessionNotesResult:
    notes_written: int


def publish_session_notes(*, config: AppConfig, day: str) -> PublishSessionNotesResult:
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
        note_path.write_text(_session_note_text(session), encoding="utf-8")
    return PublishSessionNotesResult(notes_written=len(sessions))


def _session_note_text(session: dict[str, object]) -> str:
    session_id = str(session["session_id"])
    summary_json = session.get("summary_json")
    summary = json.loads(str(summary_json)) if summary_json else None
    title = summary["headline"] if summary else f"Session {session_id}"
    managed_lines = _summary_lines(session, summary)
    return "\n".join(
        [
            f"# {title}",
            "",
            f'<!-- pcn:managed start type="session_summary" session_id="{session_id}" -->',
            *managed_lines,
            f'<!-- pcn:managed end type="session_summary" session_id="{session_id}" -->',
            "",
            "## User Notes",
            "",
        ]
    )


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
