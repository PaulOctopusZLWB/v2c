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


def reviewed_segments_for_session(*, config: AppConfig, session_id: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select ts.segment_id, ts.text, ts.speaker, ts.start_ms, ts.end_ms,
                   coalesce(r.status, 'pending_review') as review_status, r.note
            from transcript_segments ts
            left join transcript_segment_reviews r on r.segment_id = ts.segment_id
            where ts.session_id = ? and ts.is_active = 1
            order by ts.start_ms, ts.segment_id
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
    accepted = 0
    for row in rows:
        if row["review_status"] == "pending_review":
            review_segment(config=config, segment_id=str(row["segment_id"]), status="accepted", note="")
            accepted += 1
    return {"accepted": accepted}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
