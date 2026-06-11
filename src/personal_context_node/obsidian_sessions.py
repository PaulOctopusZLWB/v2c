from __future__ import annotations

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
            select session_id, date_key, started_at, ended_at, segment_count, active_speech_ms
            from sessions
            where date_key = ?
            order by started_at
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
    return "\n".join(
        [
            f"# Session {session_id}",
            "",
            f'<!-- pcn:managed start type="session_summary" session_id="{session_id}" -->',
            f"started_at: {session['started_at']}",
            f"ended_at: {session['ended_at']}",
            f"segment_count: {session['segment_count']}",
            f"active_speech_ms: {session['active_speech_ms']}",
            "",
            "完整转写不进入 session note；需要时从 SQLite transcript_segments 查询。",
            f'<!-- pcn:managed end type="session_summary" session_id="{session_id}" -->',
            "",
            "## User Notes",
            "",
        ]
    )
