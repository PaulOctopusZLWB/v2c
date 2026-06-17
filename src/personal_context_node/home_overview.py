from __future__ import annotations

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.transcript_review import review_queue


def home_overview(*, config: AppConfig) -> dict[str, object]:
    """Aggregate the 首页 (home) dashboard in a handful of grouped queries.

    Returns the actionable-landing payload:
      - review:   {pending_sessions, pending_segments} from the review queue.
      - people:   {total, enrolled} (persons count; person_voiceprints count).
      - coverage: {days, sessions, segments, embedded, emoted} corpus stats.
      - recent_sessions: the 5 most recent sessions, each with its review_status.
      - latest_day: the most recent day string (for deep-linking 观点), or None.

    Per-row work is capped at the 5 recent sessions (their review_status is computed
    from the same single review join); everything else is grouped/aggregate scalars.
    """
    # The review queue already groups pending sessions across every day; derive the two
    # headline counts from it instead of issuing another scan.
    queue = review_queue(config=config, limit=10_000)
    pending_sessions = len(queue)
    pending_segments = sum(int(row["pending"]) for row in queue)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        people = fetch_all(
            conn,
            """
            select
              (select count(*) from persons) as total,
              (select count(*) from person_voiceprints) as enrolled
            """,
        )[0]
        coverage = fetch_all(
            conn,
            """
            select
              (select count(distinct date_key) from sessions) as days,
              (select count(*) from sessions) as sessions,
              (select count(*) from transcript_segments where is_active = 1) as segments,
              (select count(*) from segment_embeddings) as embedded,
              (select count(*) from segment_emotions) as emoted
            """,
        )[0]
        # Top 5 most recent sessions, with a per-session review_status computed from the same
        # left join over reviews (no N+1 fetch). pending = active segments with no review row;
        # has_flag = any needs_fix. 'blocked' when no active segments or any needs_fix; else
        # 'pending_review' if anything is still pending; else 'accepted'.
        recent_rows = fetch_all(
            conn,
            """
            select
              s.session_id as session_id,
              s.date_key as day,
              s.started_at as started_at,
              count(ts.segment_id) as segment_count,
              count(*) filter (where ts.is_active = 1 and r.status is null) as pending,
              count(*) filter (where ts.is_active = 1) as active_total,
              max(case when r.status = 'needs_fix' then 1 else 0 end) as has_flag
            from sessions s
            left join transcript_segments ts on ts.session_id = s.session_id and ts.is_active = 1
            left join transcript_segment_reviews r on r.segment_id = ts.segment_id
            group by s.session_id, s.date_key, s.started_at, s.segment_count
            order by s.started_at desc
            limit 5
            """,
        )
        latest_rows = fetch_all(
            conn,
            "select date_key as day from sessions order by date_key desc limit 1",
        )
    finally:
        conn.close()

    recent_sessions = [
        {
            "session_id": row["session_id"],
            "day": row["day"],
            "started_at": row["started_at"],
            "segment_count": int(row["segment_count"] or 0),
            "review_status": _review_status(
                active_total=int(row["active_total"] or 0),
                pending=int(row["pending"] or 0),
                has_flag=int(row["has_flag"] or 0),
            ),
        }
        for row in recent_rows
    ]

    return {
        "review": {"pending_sessions": pending_sessions, "pending_segments": pending_segments},
        "people": {"total": int(people["total"] or 0), "enrolled": int(people["enrolled"] or 0)},
        "coverage": {
            "days": int(coverage["days"] or 0),
            "sessions": int(coverage["sessions"] or 0),
            "segments": int(coverage["segments"] or 0),
            "embedded": int(coverage["embedded"] or 0),
            "emoted": int(coverage["emoted"] or 0),
        },
        "recent_sessions": recent_sessions,
        "latest_day": latest_rows[0]["day"] if latest_rows else None,
    }


def _review_status(*, active_total: int, pending: int, has_flag: int) -> str:
    """Mirror transcript_review.session_review_status from grouped counts (no extra fetch)."""
    if active_total == 0 or has_flag:
        return "blocked"
    if pending > 0:
        return "pending_review"
    return "accepted"
