from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from personal_context_node import llm_results
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.transcript_review import reviewed_segments_for_session


def session_fingerprint(segments: list[dict]) -> str:
    """A stable sha256 hex over the session's segments, in order.

    Each segment contributes ``segment_id|text|speaker_component``, where the speaker
    component is ``person_label or speaker`` so a speaker correction (which surfaces as a
    resolved person_label) marks any prior generated 观点 as stale. Deterministic and
    order-sensitive: reordering, editing text, or relabeling a speaker all change the hash.
    """
    hasher = hashlib.sha256()
    for segment in segments:
        speaker = segment.get("person_label") or segment.get("speaker") or ""
        line = f"{segment.get('segment_id', '')}|{segment.get('text', '')}|{speaker}\n"
        hasher.update(line.encode("utf-8"))
    return hasher.hexdigest()


def viewpoint_state(*, config: AppConfig, session_id: str) -> dict:
    """The READ side of a session's 观点 workspace.

    Combines the live (reviewed) segments, the generated session summary (if any), and the
    edit/publish sidecar row into one payload. ``effective`` is the edited doc when present,
    else the generated doc, else None. ``stale`` is True only when there IS a generated doc
    and a stored fingerprint that no longer matches the live segments.
    """
    rows = reviewed_segments_for_session(config=config, session_id=session_id)
    segments = [
        {
            "segment_id": row["segment_id"],
            "text": row["text"],
            "speaker": row["speaker"],
            "person_label": row["person_label"],
        }
        for row in rows
    ]

    summary = llm_results.session_summary(config=config, session_id=session_id)
    generated = summary["content"] if summary is not None else None
    has_generated = generated is not None

    sidecar = _load_sidecar(config=config, session_id=session_id)
    status = str(sidecar["status"]) if sidecar is not None else "draft"
    edited_json = sidecar["edited_content_json"] if sidecar is not None else None
    edited = json.loads(str(edited_json)) if edited_json else None
    source_fingerprint = sidecar["source_fingerprint"] if sidecar is not None else None
    published_at = sidecar["published_at"] if sidecar is not None else None
    note_path = sidecar["note_path"] if sidecar is not None else None

    effective = edited if edited is not None else generated

    stale = bool(
        has_generated
        and source_fingerprint is not None
        and source_fingerprint != session_fingerprint(segments)
    )

    return {
        "session_id": session_id,
        "segments": segments,
        "has_generated": has_generated,
        "generated": generated,
        "edited": edited,
        "effective": effective,
        "status": status,
        "stale": stale,
        "published_at": published_at,
        "note_path": note_path,
    }


def set_segment_text(*, config: AppConfig, segment_id: str, text: str) -> bool:
    """Update a transcript segment's text (trimmed). Returns whether a row matched (404 semantics).

    Staleness is derived on read from the live fingerprint, so no extra writes happen here.
    """
    trimmed = text.strip()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        cursor = conn.execute(
            "update transcript_segments set text = ? where segment_id = ?",
            (trimmed, segment_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _load_sidecar(*, config: AppConfig, session_id: str) -> dict | None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute(
            """
            select edited_content_json, prompt_override, status, source_fingerprint,
                   note_path, published_at, updated_at
            from session_viewpoint_state
            where session_id = ?
            """,
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
