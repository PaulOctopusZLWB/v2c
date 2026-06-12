from __future__ import annotations

from dataclasses import dataclass

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


@dataclass(frozen=True)
class DailySystemSummary:
    day: str
    jobs_total: int
    jobs_succeeded: int
    jobs_failed: int
    tasks_pending: int
    tasks_failed: int
    archived_records: int
    audio_files_imported: int
    transcript_segments: int
    memory_candidates: int
    signed_events: int


def daily_system_summary(*, config: AppConfig, day: str) -> DailySystemSummary:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return DailySystemSummary(
            day=day,
            jobs_total=_count(conn, "job_runs", "started_at", day),
            jobs_succeeded=_count(conn, "job_runs", "started_at", day, status="succeeded"),
            jobs_failed=_count(conn, "job_runs", "started_at", day, status="failed"),
            tasks_pending=_count(conn, "tasks", "updated_at", day, status="pending"),
            tasks_failed=_count_statuses(conn, "tasks", "updated_at", day, statuses=("failed_retryable", "failed_terminal")),
            archived_records=_count(conn, "archive_records", "archived_at", day, status="verified"),
            audio_files_imported=_count(conn, "audio_files", "recorded_at", day),
            transcript_segments=_count_transcript_segments(conn, day),
            memory_candidates=_count(conn, "memory_candidates", "date_key", day),
            signed_events=_count(conn, "signed_events", "created_at", day),
        )
    finally:
        conn.close()


def _count(conn, table: str, timestamp_column: str, day: str, *, status: str | None = None) -> int:
    if status is None:
        row = conn.execute(
            f"select count(*) as count from {table} where substr({timestamp_column}, 1, 10) = ?",
            (day,),
        ).fetchone()
    else:
        row = conn.execute(
            f"select count(*) as count from {table} where substr({timestamp_column}, 1, 10) = ? and status = ?",
            (day, status),
        ).fetchone()
    return int(row["count"])


def _count_statuses(conn, table: str, timestamp_column: str, day: str, *, statuses: tuple[str, ...]) -> int:
    placeholders = ", ".join("?" for _status in statuses)
    row = conn.execute(
        f"select count(*) as count from {table} where substr({timestamp_column}, 1, 10) = ? and status in ({placeholders})",
        (day, *statuses),
    ).fetchone()
    return int(row["count"])


def _count_transcript_segments(conn, day: str) -> int:
    row = conn.execute(
        """
        select count(*) as count
        from transcript_segments ts
        join audio_files af on af.audio_file_id = ts.audio_file_id
        where substr(af.recorded_at, 1, 10) = ?
          and ts.is_active = 1
        """,
        (day,),
    ).fetchone()
    return int(row["count"])
