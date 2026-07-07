from __future__ import annotations

from datetime import datetime, timezone

from personal_context_node import speaker_embeddings
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
    conn = connect(config.database_path)
    try:
        initialize(conn)
        # Chunk so accepting a whole multi-thousand-segment session (6 bind vars per id) never
        # trips SQLite's per-statement variable limit. All chunks share one transaction.
        for start in range(0, len(segment_ids), 500):
            chunk = segment_ids[start : start + 500]
            values_clause = ", ".join("(?, ?, ?, ?, ?, ?)" for _ in chunk)
            params: list[object] = []
            for segment_id in chunk:
                params.extend((segment_id, status, reviewer, note, now, now))
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


def clear_review_segments(*, config: AppConfig, segment_ids: list[str]) -> int:
    """Delete review rows for the given segments, reverting them to 'pending_review'.

    Returns the number of rows actually deleted (segments without a review row don't
    count). Chunked in batches of 500 so clearing a whole multi-thousand-segment session
    never trips SQLite's per-statement variable limit; all chunks share one transaction.
    """
    if not segment_ids:
        return 0
    deleted = 0
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for start in range(0, len(segment_ids), 500):
            chunk = segment_ids[start : start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            cursor = conn.execute(
                f"delete from transcript_segment_reviews where segment_id in ({placeholders})",
                chunk,
            )
            deleted += cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    return deleted


def reviewed_segments_for_session(*, config: AppConfig, session_id: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select ts.segment_id, ts.text, ts.speaker, ts.start_ms, ts.end_ms,
                   ts.absolute_start_at, ts.absolute_end_at,
                   coalesce(r.status, 'pending_review') as review_status, r.note,
                   -- Surface the RESOLVED global person (voiceprint identity) so 审核 reflects who
                   -- actually said it and updates on re-identify; null when still unattributed.
                   o.person_id as person_id, o.person_label as person_label
            from transcript_segments ts
            left join transcript_segment_reviews r on r.segment_id = ts.segment_id
            left join segment_person_overrides o on o.segment_id = ts.segment_id
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


def session_name(*, config: AppConfig, session_id: str) -> str | None:
    """The user-given session name (rename dialog), None when unset/unknown."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select name from sessions where session_id = ?", (session_id,))
    finally:
        conn.close()
    return rows[0]["name"] if rows else None


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


def review_queue(*, config: AppConfig, limit: int = 100) -> list[dict[str, object]]:
    """One ranked queue of sessions that still need review, across every day.

    For each session with >=1 pending segment (an active segment with NO review row), return:
      session_id, day (sessions.date_key), started_at,
      pending  — count of active segments with no review row,
      total    — count of active segments,
      speakers — distinct speaker count among active segments,
      has_flag — 1 if any of the session's reviews is 'needs_fix', else 0.

    Ordered by has_flag desc, pending desc, started_at desc, and capped at `limit`. Single
    grouped query (no N+1): a left join over reviews lets `count(... filter where r.status is
    null)` express "pending" and a `having pending > 0` drops fully-reviewed sessions.
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select
              s.session_id as session_id,
              s.date_key as day,
              s.started_at as started_at,
              s.name as name,
              count(*) filter (where r.status is null) as pending,
              count(*) as total,
              count(distinct ts.speaker) as speakers,
              max(case when r.status = 'needs_fix' then 1 else 0 end) as has_flag
            from transcript_segments ts
            join sessions s on s.session_id = ts.session_id
            left join transcript_segment_reviews r on r.segment_id = ts.segment_id
            where ts.is_active = 1
            group by s.session_id, s.date_key, s.started_at, s.name
            having pending > 0
            order by has_flag desc, pending desc, started_at desc
            limit ?
            """,
            (limit,),
        )
    finally:
        conn.close()


def search_transcripts(*, config: AppConfig, query: str, limit: int = 30) -> list[dict[str, object]]:
    """Case-insensitive substring search over active segment text, across every day.

    Returns the newest matches first (by absolute wall-clock start), each carrying the day
    (sessions.date_key) and speaker so the UI can jump straight to the utterance. A blank /
    whitespace-only query short-circuits to []. LIKE metacharacters in the user query
    (% _ \\) are escaped so they match literally, not as wildcards.
    """
    needle = query.strip()
    if not needle:
        return []
    # Escape \\ first so it doesn't double-escape the % / _ escapes we add next.
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select ts.segment_id, ts.session_id, s.date_key as day, ts.speaker, ts.text,
                   ts.absolute_start_at
            from transcript_segments ts
            join sessions s on s.session_id = ts.session_id
            where ts.is_active = 1 and ts.text like ? escape '\\'
            order by ts.absolute_start_at desc
            limit ?
            """,
            (pattern, limit),
        )
    finally:
        conn.close()


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
            "select session_id, started_at, segment_count, name from sessions where date_key = ? order by started_at",
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


def rename_session(*, config: AppConfig, session_id: str, name: str) -> bool:
    """Set (or clear) a session's display name. A trimmed empty string clears it back to NULL.

    Returns whether a session row actually matched (False -> the caller can 404 an unknown id).
    """
    trimmed = name.strip()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        cursor = conn.execute(
            "update sessions set name = ?, updated_at = ? where session_id = ?",
            (trimmed or None, _now(), session_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_session(*, config: AppConfig, session_id: str) -> dict[str, object]:
    """Cascading delete of a session and all of its segments' dependent rows.

    Gathers the session's segment_ids, then in ONE transaction deletes the dependent rows keyed by
    segment_id (reviews, person overrides, embeddings, emotions), the transcript_segments rows, and
    finally the sessions row. Returns {deleted, segments}. An unknown id is a no-op
    ({deleted: False, segments: 0}). Clears the projection cache afterward (the 2D voiceprint map's
    segment set changed).
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        exists = conn.execute("select 1 from sessions where session_id = ?", (session_id,)).fetchone()
        if exists is None:
            return {"deleted": False, "segments": 0}
        segment_ids = [
            str(row["segment_id"])
            for row in conn.execute(
                "select segment_id from transcript_segments where session_id = ?", (session_id,)
            ).fetchall()
        ]
        # All dependent deletes + the session row share one transaction. Chunk the segment-id
        # deletes (one bind var each) so a multi-thousand-segment session never trips SQLite's
        # per-statement variable limit.
        for start in range(0, len(segment_ids), 500):
            chunk = segment_ids[start : start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            for table in (
                "transcript_segment_reviews",
                "segment_person_overrides",
                "segment_embeddings",
                "segment_emotions",
            ):
                conn.execute(f"delete from {table} where segment_id in ({placeholders})", chunk)
        conn.execute("delete from transcript_segments where session_id = ?", (session_id,))
        conn.execute("delete from sessions where session_id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()
    # The map's segment set changed -> any cached 2D projection is now stale.
    speaker_embeddings.clear_projection_cache()
    return {"deleted": True, "segments": len(segment_ids)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
