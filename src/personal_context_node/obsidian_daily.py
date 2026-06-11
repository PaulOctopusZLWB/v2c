from __future__ import annotations

import json
from dataclasses import dataclass

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class PublishDailyNoteResult:
    notes_written: int


def publish_daily_note(*, config: AppConfig, day: str) -> PublishDailyNoteResult:
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
    note_path.write_text(_daily_note_text(day=day, summary=summary, sessions=sessions, metrics=metrics), encoding="utf-8")
    return PublishDailyNoteResult(notes_written=1)


def _daily_metrics(conn, *, day: str, sessions: list[dict[str, object]]) -> dict[str, object]:
    rows = fetch_all(
        conn,
        """
        select count(*) as file_count, coalesce(sum(duration_ms), 0) as total_duration_ms
        from audio_files
        where substr(recorded_at, 1, 10) = ?
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
) -> str:
    return "\n".join(
        [
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
        ]
    )


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
