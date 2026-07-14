"""收件箱 — the default surface: recent sessions with their attendance/finalize state.

"每次开完会打开":the newest un-finalized session is the work item. Everything here is
per-session facts (who spoke, what's confirmed, is it frozen into the export artifact yet);
no pipeline vocabulary, no machine labels.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize

router = APIRouter(prefix="/api")


@router.get("/inbox")
def inbox_route(request: Request, limit: int = 20) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    limit = max(1, min(int(limit), 100))
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select
              s.session_id, s.date_key, s.name, s.started_at, s.ended_at,
              (select count(*) from transcript_segments ts
                 where ts.session_id = s.session_id and ts.is_active = 1) as segment_count,
              (select count(*) from transcript_segments ts
                 join segment_person_overrides o on o.segment_id = ts.segment_id
                 where ts.session_id = s.session_id and ts.is_active = 1
                   and o.person_id is not null) as attributed_count,
              (select count(*) from transcript_segments ts
                 where ts.session_id = s.session_id and ts.is_active = 1
                   and ts.speaker = 'self') as self_count,
              f.finalized_at, f.export_md_path
            from sessions s
            left join session_finalizations f on f.session_id = s.session_id
            order by s.started_at desc
            limit ?
            """,
            (limit,),
        )
        sessions: list[dict[str, object]] = []
        pending = 0
        for row in rows:
            participants = fetch_all(
                conn,
                """
                select sp.status, p.display_name
                from session_participants sp join persons p on p.person_id = sp.person_id
                where sp.session_id = ? order by sp.status, p.display_name
                """,
                (str(row["session_id"]),),
            )
            finalized = (
                {"finalized_at": str(row["finalized_at"]), "export_md_path": str(row["export_md_path"])}
                if row["finalized_at"] is not None
                else None
            )
            if finalized is None:
                pending += 1
            segment_count = int(row["segment_count"])
            attributed = int(row["attributed_count"])
            self_count = int(row["self_count"])
            sessions.append(
                {
                    "session_id": str(row["session_id"]),
                    "date_key": str(row["date_key"]),
                    "name": row["name"],
                    "started_at": str(row["started_at"]),
                    "ended_at": str(row["ended_at"]),
                    "segment_count": segment_count,
                    "attributed_count": attributed,
                    # Voices needing a human verdict: active, not the owner, not yet attributed.
                    "unidentified_count": max(0, segment_count - attributed - self_count),
                    "present": [str(p["display_name"]) for p in participants if p["status"] == "present"],
                    "absent_count": sum(1 for p in participants if p["status"] == "absent"),
                    "finalized": finalized,
                }
            )
    finally:
        conn.close()
    return {"sessions": sessions, "pending": pending}
