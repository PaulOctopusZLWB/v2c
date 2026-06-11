from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class PublishDailyNoteResult:
    notes_written: int


def publish_daily_note(*, config: AppConfig, day: str, source_run_id: str | None = None) -> PublishDailyNoteResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select content_json
            from summaries
            where summary_type = 'daily'
              and target_type = 'date_key'
              and target_id = ?
              and prompt_version = 'llm_port.daily_summary.v1'
            """,
            (day,),
        )
        if not rows:
            return PublishDailyNoteResult(notes_written=0)
        summary = json.loads(str(rows[0]["content_json"]))
        sessions = fetch_all(
            conn,
            """
            select session_id, started_at, ended_at, segment_count, active_speech_ms
            from sessions
            where date_key = ?
            order by started_at
            """,
            (day,),
        )
        metrics = _daily_metrics(conn, day=day, sessions=sessions)
    finally:
        conn.close()

    output_dir = config.obsidian_vault / "10_Daily"
    output_dir.mkdir(parents=True, exist_ok=True)
    note_path = output_dir / f"{day}.md"
    existing_text = note_path.read_text(encoding="utf-8") if note_path.exists() else None
    note_path.write_text(
        _daily_note_text(
            day=day,
            summary=summary,
            sessions=sessions,
            metrics=metrics,
            existing_text=existing_text,
            source_run_id=source_run_id,
        ),
        encoding="utf-8",
    )
    return PublishDailyNoteResult(notes_written=1)


def _daily_metrics(conn, *, day: str, sessions: list[dict[str, object]]) -> dict[str, object]:
    rows = fetch_all(
        conn,
        """
        with daily_audio as (
          select distinct af.audio_file_id, af.duration_ms
          from sessions s
          join transcript_segments ts on ts.session_id = s.session_id
          join audio_files af on af.audio_file_id = ts.audio_file_id
          where s.date_key = ?
        )
        select count(*) as file_count, coalesce(sum(duration_ms), 0) as total_duration_ms
        from daily_audio
        """,
        (day,),
    )
    return {
        "file_count": rows[0]["file_count"],
        "total_duration_ms": rows[0]["total_duration_ms"],
        "active_speech_ms": sum(int(session["active_speech_ms"]) for session in sessions),
        "session_count": len(sessions),
    }


def _daily_note_text(
    *,
    day: str,
    summary: dict[str, object],
    sessions: list[dict[str, object]],
    metrics: dict[str, object],
    existing_text: str | None = None,
    source_run_id: str | None = None,
) -> str:
    user_notes = _existing_user_notes(existing_text)
    return "\n".join(
        [
            "---",
            "pcn_schema: markdown_note.v1",
            "note_type: daily",
            f"date_key: {day}",
            "generated_by: personal-context-node",
            f"generated_at: {datetime.now(timezone.utc).isoformat()}",
            *([f"source_run_id: {source_run_id}"] if source_run_id else []),
            "pcn_managed: true",
            "---",
            "",
            f"# {day}",
            "",
            f'<!-- pcn:managed start type="daily_headline" date_key="{day}" -->',
            f"## {summary['headline']}",
            "",
            str(summary["summary"]),
            f'<!-- pcn:managed end type="daily_headline" date_key="{day}" -->',
            "",
            f'<!-- pcn:managed start type="daily_metrics" date_key="{day}" -->',
            f"- Total imported files: {metrics['file_count']}",
            f"- Total duration ms: {metrics['total_duration_ms']}",
            f"- Active speech ms: {metrics['active_speech_ms']}",
            f"- Sessions: {metrics['session_count']}",
            f'<!-- pcn:managed end type="daily_metrics" date_key="{day}" -->',
            "",
            f'<!-- pcn:managed start type="daily_sessions" date_key="{day}" -->',
            *_session_lines(day=day, sessions=sessions),
            f'<!-- pcn:managed end type="daily_sessions" date_key="{day}" -->',
            "",
            f'<!-- pcn:managed start type="daily_todos" date_key="{day}" -->',
            *_todo_lines(summary.get("todos_rollup", [])),
            f'<!-- pcn:managed end type="daily_todos" date_key="{day}" -->',
            "",
            f'<!-- pcn:managed start type="daily_decisions" date_key="{day}" -->',
            *_decision_lines(summary.get("decisions_rollup", [])),
            f'<!-- pcn:managed end type="daily_decisions" date_key="{day}" -->',
            "",
            "## User Notes",
            "",
            '<!-- pcn:user start type="user_notes" -->',
            user_notes,
            '<!-- pcn:user end type="user_notes" -->',
        ]
    )


def _existing_user_notes(existing_text: str | None) -> str:
    if not existing_text:
        return ""
    match = re.search(
        r'<!-- pcn:user start type="user_notes" -->\n?(.*?)\n?<!-- pcn:user end type="user_notes" -->',
        existing_text,
        flags=re.DOTALL,
    )
    return match.group(1).rstrip("\n") if match else ""


def _session_lines(*, day: str, sessions: list[dict[str, object]]) -> list[str]:
    return [
        f"- [[20_Conversations/{day}/{session['session_id']}|{session['session_id']}]]"
        for session in sessions
    ] or ["- No sessions"]


def _todo_lines(items: object) -> list[str]:
    if not isinstance(items, list) or not items:
        return ["- No todos"]
    return [
        f"- {item['text']} (owner: {item['owner']}, session: {item['session_id']})"
        for item in items
        if isinstance(item, dict)
    ]


def _decision_lines(items: object) -> list[str]:
    if not isinstance(items, list) or not items:
        return ["- No decisions"]
    return [
        f"- {item['text']} (session: {item['session_id']})"
        for item in items
        if isinstance(item, dict)
    ]
