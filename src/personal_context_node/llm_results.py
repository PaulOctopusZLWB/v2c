from __future__ import annotations

import json

from personal_context_node.config import AppConfig
from personal_context_node.evidence_refs import evidence_ids_from_candidate_json
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def _latest_summary(config: AppConfig, *, summary_type: str, target_id: str) -> dict[str, object] | None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select content_json, model_name, updated_at
            from summaries
            where summary_type = ? and target_id = ?
            order by updated_at desc
            limit 1
            """,
            (summary_type, target_id),
        )
    finally:
        conn.close()
    if not rows:
        return None
    return {
        "content": json.loads(str(rows[0]["content_json"])),
        "model_name": rows[0]["model_name"],
        "updated_at": rows[0]["updated_at"],
    }


def session_summary(*, config: AppConfig, session_id: str) -> dict[str, object] | None:
    result = _latest_summary(config, summary_type="session", target_id=session_id)
    return None if result is None else {"session_id": session_id, **result}


def daily_context(*, config: AppConfig, day: str) -> dict[str, object] | None:
    result = _latest_summary(config, summary_type="daily", target_id=day)
    return None if result is None else {"day": day, **result}


def day_memory_candidates(*, config: AppConfig, day: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        candidates = fetch_all(
            conn,
            """
            select candidate_id, candidate_claim, edited_claim, claim_type, confidence, status,
                   evidence_refs_json
            from memory_candidates
            where date_key = ?
            order by created_at
            """,
            (day,),
        )
        for candidate in candidates:
            candidate["evidence_segment_ids"] = _resolve_evidence_segment_ids(
                conn, str(candidate.pop("evidence_refs_json"))
            )
        return candidates
    finally:
        conn.close()


def _resolve_evidence_segment_ids(conn: object, evidence_refs_json: str) -> list[str]:
    """Resolve a candidate's evidence refs to distinct transcript segment ids.

    ``memory_candidates.evidence_refs_json`` is a JSON array of evidence ref ids
    (or dicts carrying ``evidence_id``). Each evidence id maps, via
    ``evidence_refs.evidence_id`` -> ``evidence_refs.source_id``, to a
    ``transcript_segments.segment_id`` (for ``source_type = 'transcript_segment'``
    the writer stores the segment id in ``source_id``). Returns the distinct
    segment ids that resolve, preserving the evidence-ref order; empty when none.
    """
    evidence_ids = evidence_ids_from_candidate_json(evidence_refs_json)
    if not evidence_ids:
        return []
    placeholders = ",".join("?" for _ in evidence_ids)
    rows = conn.execute(  # type: ignore[attr-defined]
        f"""
        select evidence_id, source_id
        from evidence_refs
        where evidence_id in ({placeholders})
        """,
        tuple(evidence_ids),
    ).fetchall()
    source_id_by_evidence = {str(row["evidence_id"]): str(row["source_id"]) for row in rows}
    segment_ids: list[str] = []
    seen: set[str] = set()
    for evidence_id in evidence_ids:
        source_id = source_id_by_evidence.get(evidence_id)
        if source_id is None or source_id in seen:
            continue
        seen.add(source_id)
        segment_ids.append(source_id)
    return segment_ids
