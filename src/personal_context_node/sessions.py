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
              ts.chunk_id,
              ts.start_ms,
              ts.end_ms,
              af.recorded_at,
              af.source_device,
              attr.person_id
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            left join v_segment_attribution attr on attr.segment_id = ts.segment_id
            where substr(af.recorded_at, 1, 10) = ? and ts.is_active = 1
            order by af.source_device, af.recorded_at, ts.start_ms, ts.segment_id
            """,
            (day,),
        )
        groups = _group_segments(segments, gap_ms=session_gap_minutes * 60 * 1000)
        now = datetime.now(timezone.utc).isoformat()
        # Key the existing-session lookup and delete by the FILE recorded-day (the unit
        # being re-derived), not by date_key — a cross-midnight session is attributed to
        # its started_at date (§25.3 rule 2), so its date_key may differ from `day`.
        existing_rows = fetch_all(
            conn,
            """
            select s.first_segment_id, s.session_id, s.exclude_from_memory, ts.chunk_id as first_chunk_id
            from sessions s
            join transcript_segments ts on ts.segment_id = s.first_segment_id
            join audio_files af on af.audio_file_id = ts.audio_file_id
            where substr(af.recorded_at, 1, 10) = ?
            """,
            (day,),
        )
        existing_by_first_segment = {str(row["first_segment_id"]): row for row in existing_rows}
        # Anchor reuse on the first segment's CHUNK too: an ASR re-run replaces every
        # segment id (old ones go is_active=0), so first-segment containment can't match;
        # but chunks are stable, so the regrouped session's first chunk identifies the
        # same session and its id stays stable (§26.2.7, §36.2.6).
        existing_by_first_chunk = {
            str(row["first_chunk_id"]): row for row in existing_rows if row["first_chunk_id"] is not None
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
        conn.execute(
            """
            delete from sessions where session_id in (
              select s.session_id
              from sessions s
              join transcript_segments ts on ts.segment_id = s.first_segment_id
              join audio_files af on af.audio_file_id = ts.audio_file_id
              where substr(af.recorded_at, 1, 10) = ?
            )
            """,
            (day,),
        )
        assigned = 0
        used_session_ids: set[str] = set()
        for group in groups:
            first_segment_id = str(group[0]["segment_id"])
            group_segment_ids = [str(row["segment_id"]) for row in group]
            # Reuse an existing session_id when this group CONTAINS that session's
            # first segment (rule 26.2.7) — not only when it starts with it — so note
            # filenames and [[ses_*]] references do not drift when a rerun extends a
            # session with earlier audio.
            reused = None
            for segment_id in group_segment_ids:
                candidate = existing_by_first_segment.get(segment_id)
                if candidate is not None and str(candidate["session_id"]) not in used_session_ids:
                    reused = candidate
                    break
            if reused is None:
                # Fallback: match by the group's first chunk (survives an ASR re-run that
                # replaced all segment ids).
                chunk_candidate = existing_by_first_chunk.get(str(group[0]["chunk_id"]))
                if chunk_candidate is not None and str(chunk_candidate["session_id"]) not in used_session_ids:
                    reused = chunk_candidate
            session_id = str(reused["session_id"]) if reused is not None else f"ses_{uuid4().hex}"
            used_session_ids.add(session_id)
            # Preserve any prior exclude_from_memory flag across reruns (§38.5): if any
            # existing session folded into this group (by first-segment or first-chunk)
            # was excluded, keep it excluded.
            group_chunk_ids = {str(row["chunk_id"]) for row in group}
            exclude_from_memory = 1 if any(
                int(row["exclude_from_memory"])
                for row in existing_rows
                if str(row["first_segment_id"]) in group_segment_ids
                or str(row["first_chunk_id"]) in group_chunk_ids
            ) else 0
            started_at = _absolute_time(group[0])
            ended_at = _absolute_time({**group[-1], "start_ms": group[-1]["end_ms"]})
            # Attribute the session to the date of its started_at (§25.3 rule 2,
            # cross_midnight_policy = start_date), which may differ from the file's day.
            date_key = started_at[:10]
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
                    date_key,
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
    previous_device: object = None
    for row in rows:
        start_ms = _absolute_epoch_ms(row, offset_field="start_ms")
        device = row.get("source_device")
        # Only same-device, same-day segments within the gap threshold share a session
        # (rule 26.2.1): a device change always starts a new session.
        gap_break = current and previous_end is not None and start_ms - previous_end > gap_ms
        device_break = current and device != previous_device
        if gap_break or device_break:
            groups.append(current)
            current = []
        current.append(row)
        previous_end = _absolute_epoch_ms(row, offset_field="end_ms")
        previous_device = device
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
