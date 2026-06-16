from __future__ import annotations

from datetime import datetime, timezone

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


VALID_REVIEW_STATUSES = {"pending_review", "accepted", "rejected", "needs_fix"}


def accepted_segments_clause(alias: str = "ts") -> str:
    """The single source of the LLM acceptance gate predicate.

    Callers paste this into a WHERE clause (with a leading 'and') only when
    config.require_accepted_transcripts is True.
    """
    return (
        f"and exists (select 1 from transcript_segment_reviews review "
        f"where review.segment_id = {alias}.segment_id and review.status = 'accepted')"
    )


def review_segment(*, config: AppConfig, segment_id: str, status: str, note: str = "", reviewer: str = "local_user") -> None:
    if status not in VALID_REVIEW_STATUSES - {"pending_review"}:
        raise ValueError(f"invalid transcript review status: {status}")
    now = _now()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into transcript_segment_reviews (segment_id, status, reviewer, note, reviewed_at, updated_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(segment_id) do update set
              status = excluded.status, reviewer = excluded.reviewer, note = excluded.note,
              reviewed_at = excluded.reviewed_at, updated_at = excluded.updated_at
            """,
            (segment_id, status, reviewer, note, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def batch_review_segments(*, config: AppConfig, segment_ids: list[str], status: str, note: str = "", reviewer: str = "local_user") -> int:
    if status not in VALID_REVIEW_STATUSES - {"pending_review"}:
        raise ValueError(f"invalid transcript review status: {status}")
    if not segment_ids:
        return 0
    now = _now()
    values_clause = ", ".join("(?, ?, ?, ?, ?, ?)" for _ in segment_ids)
    params: list[object] = []
    for segment_id in segment_ids:
        params.extend((segment_id, status, reviewer, note, now, now))
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            f"""
            insert into transcript_segment_reviews (segment_id, status, reviewer, note, reviewed_at, updated_at)
            values {values_clause}
            on conflict(segment_id) do update set
              status = excluded.status, reviewer = excluded.reviewer, note = excluded.note,
              reviewed_at = excluded.reviewed_at, updated_at = excluded.updated_at
            """,
            params,
        )
        conn.commit()
    finally:
        conn.close()
    return len(segment_ids)


def reviewed_segments_for_session(*, config: AppConfig, session_id: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select ts.segment_id, ts.text, ts.speaker, ts.start_ms, ts.end_ms,
                   ts.absolute_start_at, ts.absolute_end_at,
                   coalesce(r.status, 'pending_review') as review_status, r.note
            from transcript_segments ts
            left join transcript_segment_reviews r on r.segment_id = ts.segment_id
            where ts.session_id = ? and ts.is_active = 1
            -- A whole-day session fans in many audio files; start_ms is per-file, so it would
            -- interleave files and show 00:00 for each file's opening segments. Order by the
            -- absolute wall-clock timeline; start_ms is a tiebreak for any untimed (chunk-mode) rows.
            order by ts.absolute_start_at, ts.start_ms, ts.segment_id
            """,
            (session_id,),
        )
    finally:
        conn.close()


def session_review_status(*, config: AppConfig, session_id: str) -> str:
    rows = reviewed_segments_for_session(config=config, session_id=session_id)
    statuses = {str(r["review_status"]) for r in rows}
    if not rows or "needs_fix" in statuses:
        return "blocked"
    if "pending_review" in statuses:
        return "pending_review"
    return "accepted"


def accept_remaining_segments(*, config: AppConfig, session_id: str) -> dict[str, int]:
    rows = reviewed_segments_for_session(config=config, session_id=session_id)
    pending_ids = [str(row["segment_id"]) for row in rows if row["review_status"] == "pending_review"]
    accepted = batch_review_segments(config=config, segment_ids=pending_ids, status="accepted", note="")
    return {"accepted": accepted}


def list_days(*, config: AppConfig) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select date_key as day, count(*) as session_count
            from sessions
            group by date_key
            order by date_key desc
            """,
        )
    finally:
        conn.close()


def sessions_for_day(*, config: AppConfig, day: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        sessions = fetch_all(
            conn,
            "select session_id, started_at, segment_count from sessions where date_key = ? order by started_at",
            (day,),
        )
    finally:
        conn.close()
    # review_status is computed per session via the existing helper (N+1 is fine for a local single-user panel).
    for session in sessions:
        session["review_status"] = session_review_status(config=config, session_id=str(session["session_id"]))
    return sessions


def day_status_rows(*, config: AppConfig) -> list[dict[str, object]]:
    """Return one row per recorded day with a processing/ready status aggregate.

    Status logic:
    - 'ready': the day has at least one session AND all tasks whose target traces to
      this day are in a terminal state (succeeded, failed_terminal, or retry-exhausted).
    - 'processing': otherwise (still has pending/claimed/running/retryable tasks, or
      no sessions yet).

    Uses a single grouped query — no N+1.
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            with day_tasks as (
              -- vad / asr tasks: keyed to audio_file's recorded day
              select substr(af.recorded_at, 1, 10) as day, t.status, t.retry_count, t.max_retries
              from tasks t
              join audio_files af on af.audio_file_id = t.target_id
              where t.task_type in ('vad', 'asr')
                and t.target_type in ('audio_file', 'audio_chunk')
              -- for asr tasks keyed on audio_chunk, resolve the chunk's file day
              union all
              select substr(af.recorded_at, 1, 10) as day, t.status, t.retry_count, t.max_retries
              from tasks t
              join audio_chunks ac on ac.chunk_id = t.target_id
              join audio_files af on af.audio_file_id = ac.audio_file_id
              where t.task_type = 'asr' and t.target_type = 'audio_chunk'
              -- session_derive / daily_generate / obsidian_publish: target_id IS the date_key
              union all
              select t.target_id as day, t.status, t.retry_count, t.max_retries
              from tasks t
              where t.task_type in ('session_derive', 'daily_generate', 'obsidian_publish')
                and t.target_type = 'date_key'
              -- summarize_session: target_id is a SESSION id; resolve it to the session's day
              union all
              select s.date_key as day, t.status, t.retry_count, t.max_retries
              from tasks t
              join sessions s on s.session_id = t.target_id
              where t.task_type = 'summarize_session' and t.target_type = 'session'
            ),
            day_sessions as (
              select date_key as day, count(*) as session_count
              from sessions
              group by date_key
            ),
            day_agg as (
              select
                dt.day,
                sum(case
                  when dt.status in ('pending', 'claimed', 'running') then 1
                  when dt.status = 'failed_retryable' and dt.retry_count < dt.max_retries then 1
                  else 0
                end) as active_count,
                count(*) as total_count
              from day_tasks dt
              group by dt.day
            )
            select
              da.day,
              coalesce(ds.session_count, 0) as session_count,
              da.active_count,
              da.total_count,
              case
                when coalesce(ds.session_count, 0) > 0 and da.active_count = 0 then 'ready'
                else 'processing'
              end as status
            from day_agg da
            left join day_sessions ds on ds.day = da.day
            order by da.day desc
            """,
        )
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
