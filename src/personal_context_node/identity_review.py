from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


PARTICIPANT_STATUSES = {"present", "absent", "uncertain"}


def set_session_participant(
    *,
    config: AppConfig,
    session_id: str,
    person_id: str,
    status: str,
    note: str | None = None,
    source: str = "manual",
) -> dict[str, object]:
    if status not in PARTICIPANT_STATUSES:
        raise ValueError(f"invalid participant status: {status}")
    now = _now()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        person = conn.execute("select display_name from persons where person_id = ?", (person_id,)).fetchone()
        if person is None:
            raise ValueError(f"unknown person_id: {person_id}")
        conn.execute(
            """
            insert into session_participants (session_id, person_id, status, source, note, updated_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(session_id, person_id) do update set
              status = excluded.status,
              source = excluded.source,
              note = excluded.note,
              updated_at = excluded.updated_at
            """,
            (session_id, person_id, status, source, note, now),
        )
        conn.commit()
        return {"person_id": person_id, "display_name": str(person["display_name"]), "status": status}
    finally:
        conn.close()


def record_not_person(
    *,
    config: AppConfig,
    session_id: str,
    segment_ids: list[str],
    person_id: str,
    note: str | None = None,
    source: str = "manual",
) -> int:
    if not segment_ids:
        return 0
    now = _now()
    unique_segment_ids = list(dict.fromkeys(segment_ids))
    conn = connect(config.database_path)
    try:
        initialize(conn)
        if conn.execute("select 1 from persons where person_id = ?", (person_id,)).fetchone() is None:
            raise ValueError(f"unknown person_id: {person_id}")
        cleared = 0
        for segment_id in unique_segment_ids:
            conn.execute(
                """
                insert into segment_identity_negative_feedback
                  (segment_id, person_id, session_id, source, note, updated_at)
                values (?, ?, ?, ?, ?, ?)
                on conflict(segment_id, person_id) do update set
                  session_id = excluded.session_id,
                  source = excluded.source,
                  note = excluded.note,
                  updated_at = excluded.updated_at
                """,
                (segment_id, person_id, session_id, source, note, now),
            )
            # "不是 X" is the most recent human verdict on this exact (segment, person) pair:
            # the contradicted attribution goes too — manual included, the user just overrode
            # their earlier label. auto_attribute consults the feedback, so it can't come back.
            cur = conn.execute(
                "delete from segment_person_overrides where segment_id = ? and person_id = ?",
                (segment_id, person_id),
            )
            cleared += int(cur.rowcount)
        conn.commit()
    finally:
        conn.close()
    if cleared:
        from personal_context_node.speaker_embeddings import clear_projection_results_cache

        clear_projection_results_cache()  # attributions recolor points; fitted coords stay valid
    return len(unique_segment_ids)


def identity_review_for_session(*, config: AppConfig, session_id: str) -> dict[str, object]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        participants = _participants(conn, session_id=session_id)
        participant_status = {str(row["person_id"]): str(row["status"]) for row in participants}
        negative = _negative_by_segment(conn, session_id=session_id)
        rows = _attribution_rows(conn, session_id=session_id)
        negative_count = int(
            conn.execute(
                "select count(*) c from segment_identity_negative_feedback where session_id = ?",
                (session_id,),
            ).fetchone()["c"]
        )
    finally:
        conn.close()

    unknown_labels = _unknown_labeler()
    grouped: dict[str, dict[str, object]] = {}
    new_person_candidates: dict[str, dict[str, object]] = {}
    for row in rows:
        segment_id = str(row["segment_id"])
        person_id = row.get("person_id")
        display_name = row.get("person_label") or row.get("display_name")
        raw_label = str(row.get("speaker_cluster_id") or row.get("speaker") or segment_id)
        text = str(row.get("text") or "")
        if person_id is None:
            candidate = new_person_candidates.setdefault(
                raw_label,
                {
                    "speaker": raw_label,
                    "status": "unknown",
                    "safe_label": unknown_labels(raw_label),
                    "segment_count": 0,
                    "segment_ids": [],
                    "sample_text": text,
                },
            )
            _add_candidate_segment(candidate, segment_id=segment_id, text=text)
            continue

        pid = str(person_id)
        status = _candidate_status(
            person_id=pid,
            segment_ids=[segment_id],
            participant_status=participant_status,
            negative=negative,
        )
        safe_label = str(display_name) if status == "trusted" else unknown_labels(pid)
        candidate = grouped.setdefault(
            pid,
            {
                "person_id": pid,
                "display_name": str(display_name or pid),
                "status": status,
                "safe_label": safe_label,
                "segment_count": 0,
                "segment_ids": [],
                "sample_text": text,
                "evidence_sources": [],
            },
        )
        _add_candidate_segment(candidate, segment_id=segment_id, text=text)
        if status == "excluded":
            candidate["status"] = "excluded"
            candidate["safe_label"] = safe_label
        elif candidate["status"] != "excluded" and status == "trusted":
            candidate["status"] = "trusted"
            candidate["safe_label"] = str(display_name or pid)
        source = row.get("attribution_source")
        if source and source not in candidate["evidence_sources"]:
            candidate["evidence_sources"].append(source)

    present_count = sum(1 for row in participants if row["status"] == "present")
    from personal_context_node.session_finalize import finalization_state

    return {
        "session_id": session_id,
        "can_summarize": present_count > 0,
        # 定稿即产品终点(codex 接手认知层):门槛与 can_summarize 相同——至少一位确认出席。
        "can_finalize": present_count > 0,
        "finalized": finalization_state(config=config, session_id=session_id),
        "participants": participants,
        "candidates": sorted(grouped.values(), key=lambda item: (str(item["status"]), str(item["display_name"]))),
        "new_person_candidates": sorted(new_person_candidates.values(), key=lambda item: str(item["speaker"])),
        "mixed_clusters": [],
        "excluded_people": [item for item in grouped.values() if item["status"] == "excluded"],
        "negative_feedback_count": negative_count,
    }


def safe_llm_segments(
    *,
    config: AppConfig,
    session_id: str,
    segments: list[dict[str, object]],
    include_speaker: bool,
) -> tuple[list[dict[str, object]], str]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        participants = _participants(conn, session_id=session_id)
        present = {
            str(row["person_id"]): str(row["display_name"])
            for row in participants
            if row["status"] == "present"
        }
        negative = _negative_by_segment(conn, session_id=session_id)
    finally:
        conn.close()

    unknown_labels = _unknown_labeler()
    safe_segments: list[dict[str, object]] = []
    for row in segments:
        segment_id = str(row["segment_id"])
        safe = {
            "segment_id": row["segment_id"],
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "text": row["text"],
            "evidence_id": row["evidence_id"],
        }
        if include_speaker:
            person_id = row.get("person_id")
            pid = str(person_id) if person_id is not None else None
            if pid is not None and pid in present and pid not in negative.get(segment_id, set()):
                safe["speaker"] = present[pid]
            else:
                raw_key = str(pid or row.get("speaker_cluster_id") or row.get("speaker") or segment_id)
                safe["speaker"] = unknown_labels(raw_key)
        safe_segments.append(safe)

    names = sorted(present.values())
    if names:
        prompt_suffix = (
            f"本场确认出现的人物: {', '.join(names)}。\n"
            "进入总结时只能把这些确认人物作为真实姓名；任何不在确认名单中的归属都已替换为未确认说话人_N。\n"
            "不得输出未出现在确认名单中的人物姓名；per_speaker 只能使用确认人物或未确认说话人_N。"
        )
    else:
        prompt_suffix = (
            "本场确认出现的人物: （尚未确认）。\n"
            "不得输出任何未确认人物姓名；所有说话人只能写作未确认说话人_N。"
        )
    return safe_segments, prompt_suffix


def _participants(conn, *, session_id: str) -> list[dict[str, object]]:
    return fetch_all(
        conn,
        """
        select sp.person_id, p.display_name, sp.status
        from session_participants sp
        join persons p on p.person_id = sp.person_id
        where sp.session_id = ?
        order by case sp.status when 'present' then 0 when 'uncertain' then 1 else 2 end,
                 p.display_name, sp.person_id
        """,
        (session_id,),
    )


def _negative_by_segment(conn, *, session_id: str) -> dict[str, set[str]]:
    rows = fetch_all(
        conn,
        """
        select segment_id, person_id
        from segment_identity_negative_feedback
        where session_id = ?
        """,
        (session_id,),
    )
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(str(row["segment_id"]), set()).add(str(row["person_id"]))
    return result


def _attribution_rows(conn, *, session_id: str) -> list[dict[str, object]]:
    return fetch_all(
        conn,
        """
        select
          ts.segment_id,
          ts.speaker,
          coalesce(ts.speaker_cluster_id, ts.speaker) as speaker_cluster_id,
          ts.text,
          coalesce(o.person_id, m.person_id) as person_id,
          coalesce(o.person_label, m.person_label, p.display_name) as person_label,
          p.display_name as display_name,
          case
            when o.person_id is not null then 'segment_override'
            when m.person_id is not null then 'speaker_mapping'
            else 'raw_speaker'
          end as attribution_source
        from transcript_segments ts
        left join segment_person_overrides o on o.segment_id = ts.segment_id
        left join speaker_mappings m
          on m.speaker_cluster_id = coalesce(ts.speaker_cluster_id, ts.speaker)
          or m.speaker = coalesce(ts.speaker_cluster_id, ts.speaker)
        left join persons p on p.person_id = coalesce(o.person_id, m.person_id)
        where ts.session_id = ? and ts.is_active = 1
        order by coalesce(ts.absolute_start_at, ''), ts.start_ms, ts.segment_id
        """,
        (session_id,),
    )


def _candidate_status(
    *,
    person_id: str,
    segment_ids: Iterable[str],
    participant_status: dict[str, str],
    negative: dict[str, set[str]],
) -> str:
    if participant_status.get(person_id) == "absent":
        return "excluded"
    if any(person_id in negative.get(segment_id, set()) for segment_id in segment_ids):
        return "excluded"
    if participant_status.get(person_id) == "present":
        return "trusted"
    return "suggested"


def _add_candidate_segment(candidate: dict[str, object], *, segment_id: str, text: str) -> None:
    segment_ids = candidate["segment_ids"]
    if isinstance(segment_ids, list) and segment_id not in segment_ids:
        segment_ids.append(segment_id)
    candidate["segment_count"] = int(candidate["segment_count"]) + 1
    if not candidate.get("sample_text"):
        candidate["sample_text"] = text


def _unknown_labeler():
    labels: dict[str, str] = {}

    def label(key: str) -> str:
        if key not in labels:
            labels[key] = f"未确认说话人_{len(labels) + 1}"
        return labels[key]

    return label


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
