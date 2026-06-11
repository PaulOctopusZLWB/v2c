from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class DeriveSessionsResult:
    sessions_derived: int
    segments_assigned: int


def derive_sessions_for_day(
    *,
    config: AppConfig,
    day: str,
    session_gap_minutes: int = 20,
) -> DeriveSessionsResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        _ensure_session_columns(conn)
        segments = fetch_all(
            conn,
            """
            select
              ts.segment_id,
              ts.audio_file_id,
              ts.start_ms,
              ts.end_ms,
              af.recorded_at,
              attr.person_id
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            left join v_segment_attribution attr on attr.segment_id = ts.segment_id
            where substr(af.recorded_at, 1, 10) = ? and ts.is_active = 1
            order by af.recorded_at, ts.start_ms, ts.segment_id
            """,
            (day,),
        )
        groups = _group_segments(segments, gap_ms=session_gap_minutes * 60 * 1000)
        now = datetime.now(timezone.utc).isoformat()
        existing_by_first_segment = {
            str(row["first_segment_id"]): row
            for row in fetch_all(conn, "select first_segment_id, session_id, exclude_from_memory from sessions where date_key = ?", (day,))
        }
        conn.execute(
            """
            update transcript_segments
            set session_id = null
            where audio_file_id in (
              select audio_file_id
              from audio_files
              where substr(recorded_at, 1, 10) = ?
            )
            """,
            (day,),
        )
        conn.execute("delete from sessions where date_key = ?", (day,))
        assigned = 0
        for group in groups:
            first_segment_id = str(group[0]["segment_id"])
            existing = existing_by_first_segment.get(first_segment_id)
            session_id = str(existing["session_id"]) if existing else f"ses_{uuid4().hex}"
            exclude_from_memory = int(existing["exclude_from_memory"]) if existing else 0
            started_at = _absolute_time(group[0])
            ended_at = _absolute_time({**group[-1], "start_ms": group[-1]["end_ms"]})
            active_speech_ms = sum(int(row["end_ms"]) - int(row["start_ms"]) for row in group)
            primary_person_id = _primary_person_id(group)
            conn.execute(
                """
                insert into sessions (
                  session_id, date_key, started_at, ended_at, source,
                  segment_count, active_speech_ms, primary_person_id, first_segment_id,
                  exclude_from_memory, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    day,
                    started_at,
                    ended_at,
                    "derived_from_segments",
                    len(group),
                    active_speech_ms,
                    primary_person_id,
                    first_segment_id,
                    exclude_from_memory,
                    now,
                    now,
                ),
            )
            for row in group:
                conn.execute(
                    "update transcript_segments set session_id = ? where segment_id = ?",
                    (session_id, row["segment_id"]),
                )
                assigned += 1
        conn.commit()
        return DeriveSessionsResult(sessions_derived=len(groups), segments_assigned=assigned)
    finally:
        conn.close()


def _group_segments(rows: list[dict[str, object]], *, gap_ms: int) -> list[list[dict[str, object]]]:
    groups: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    previous_end: int | None = None
    for row in rows:
        start_ms = _absolute_epoch_ms(row, offset_field="start_ms")
        if current and previous_end is not None and start_ms - previous_end > gap_ms:
            groups.append(current)
            current = []
        current.append(row)
        previous_end = _absolute_epoch_ms(row, offset_field="end_ms")
    if current:
        groups.append(current)
    return groups


def _absolute_epoch_ms(row: dict[str, object], *, offset_field: str) -> int:
    recorded_at = datetime.fromisoformat(str(row["recorded_at"]))
    return int(recorded_at.timestamp() * 1000) + int(row[offset_field])


def _absolute_time(row: dict[str, object]) -> str:
    recorded_at = datetime.fromisoformat(str(row["recorded_at"]))
    return (recorded_at + timedelta(milliseconds=int(row["start_ms"]))).isoformat()


def _primary_person_id(group: list[dict[str, object]]) -> str | None:
    counts: dict[str, int] = {}
    for row in group:
        person_id = row.get("person_id")
        if person_id:
            counts[str(person_id)] = counts.get(str(person_id), 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _ensure_session_columns(conn) -> None:
    existing = {row["name"] for row in conn.execute("pragma table_info(transcript_segments)").fetchall()}
    if "session_id" not in existing:
        conn.execute("alter table transcript_segments add column session_id text")
        conn.commit()
