from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import LLMPort, SessionSummary
from personal_context_node.evidence_refs import persist_segment_evidence_refs
from personal_context_node.identity_review import safe_llm_segments
from personal_context_node.session_viewpoint import (
    effective_session_prompt,
    record_summary_regenerated,
    session_fingerprint,
    session_prompt_fingerprint,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.summary_schemas import validate_session_summary
from personal_context_node.transcript_review import accepted_segments_clause


logger = logging.getLogger(__name__)

PROMPT_VERSION = "llm_port.session_summary.v2"


@dataclass(frozen=True)
class SessionSummaryResult:
    summaries_created: int


def summarize_session(*, config: AppConfig, session_id: str, llm: LLMPort, force: bool = False) -> SessionSummaryResult:
    """Generate (or reuse) the session_summary for ``session_id``.

    Incremental skip: before calling the LLM, we compute the fingerprint of the current
    (reviewed) transcript segments and compare it against the fingerprint recorded for the last
    *successful* summary of this session at the current ``PROMPT_VERSION`` (stored in
    ``session_viewpoint_state.source_fingerprint`` — written by ``record_summary_regenerated``
    right after a successful generate, in the same table/column already used to compute
    ``viewpoint_state()['stale']``), and likewise the EFFECTIVE prompt's fingerprint against
    ``session_viewpoint_state.summary_prompt_fingerprint``. When both match, neither the
    transcript nor the prompt changed since the last successful run, so we skip the (slow,
    costly) LLM call entirely and report ``summaries_created=0`` — the existing summary row is
    left as-is and remains the one ``llm_results.session_summary()`` returns. Editing the prompt
    (per-session override or global template) invalidates the skip. ``force=True`` bypasses the
    skip unconditionally (no current caller passes it: process_runner.py's pipeline call and the
    routes_viewpoints.py "regenerate" button both go through the default — a regenerate with an
    unchanged transcript AND prompt is a no-op LLM-wise, and callers observe no difference in
    return-value semantics either way).
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        gate = accepted_segments_clause("ts") if config.require_accepted_transcripts else ""
        segments = fetch_all(
            conn,
            f"""
            select
              ts.segment_id,
              ts.speaker,
              coalesce(ts.speaker_cluster_id, ts.speaker) as speaker_cluster_id,
              ts.start_ms,
              ts.end_ms,
              ts.text,
              ts.evidence_id,
              coalesce(o.person_id, m.person_id) as person_id,
              coalesce(o.person_label, m.person_label) as person_label
            from transcript_segments ts
            left join segment_person_overrides o on o.segment_id = ts.segment_id
            left join speaker_mappings m
              on m.speaker_cluster_id = coalesce(ts.speaker_cluster_id, ts.speaker)
              or m.speaker = coalesce(ts.speaker_cluster_id, ts.speaker)
            where ts.session_id = ? and ts.is_active = 1
              {gate}
            order by coalesce(ts.absolute_start_at, ''), ts.start_ms, ts.segment_id
            """,
            (session_id,),
        )
        if not segments:
            return SessionSummaryResult(summaries_created=0)
        if not force and _has_fresh_summary(conn, config=config, session_id=session_id, segments=segments):
            logger.info(
                "summarize_session skipped (fingerprint unchanged): session_id=%s prompt_version=%s",
                session_id,
                PROMPT_VERSION,
            )
            return SessionSummaryResult(summaries_created=0)
        llm_segments, identity_prompt = safe_llm_segments(
            config=config,
            session_id=session_id,
            segments=segments,
            include_speaker=config.send_speaker_labels,
        )
        prompt = f"{effective_session_prompt(config=config, session_id=session_id)['effective']}\n\n{identity_prompt}"
        # Do NO DB write before the (slow, possibly multi-minute) LLM call. Holding the WAL write
        # lock across generate_session_summary would block every other writer — manual viewpoint
        # edit/publish, the segment-text PATCH, even concurrent pipeline steps — until busy_timeout
        # (30s) expires and they 500 with "database is locked". We only read segments above (read
        # lock, released), call the LLM with no write lock held, then persist the evidence refs +
        # summary together in one short transaction. Persisting AFTER validation also keeps a
        # rejected summary side-effect-free (no evidence_refs / summary rows linger on a ValueError).
        summary = _generate_session_summary_with_budget(
            llm=llm,
            session_id=session_id,
            transcript_segments=llm_segments,
            max_chunk_tokens=config.max_chunk_tokens,
            prompt=prompt,
        )
        _validate_summary_evidence_refs(summary, segments)
        persist_segment_evidence_refs(conn, segments=segments, owner_id=config.owner_did)
        _persist_session_summary(conn, summary)
        conn.commit()
        # Record the regenerate in the viewpoint sidecar: stamp the source fingerprint (so the new
        # summary reads as fresh), discard any prior manual edit + reset status to draft, keep the
        # per-session prompt_override. Done after commit on its own connection (separate DB conn).
        record_summary_regenerated(config=config, session_id=session_id)
        return SessionSummaryResult(summaries_created=1)
    finally:
        conn.close()


def _has_fresh_summary(
    conn: sqlite3.Connection, *, config: AppConfig, session_id: str, segments: list[dict[str, object]]
) -> bool:
    """True when a successful summary at PROMPT_VERSION exists AND its recorded fingerprints
    match the live state — i.e. regenerating would produce nothing new.

    Two fingerprints must BOTH match (stored on session_viewpoint_state, stamped by
    ``record_summary_regenerated`` right after each successful generate):
    - ``source_fingerprint``: the reviewed segments (text/speaker/order) — same column the
      viewpoint 'stale' flag uses;
    - ``summary_prompt_fingerprint``: the EFFECTIVE prompt (per-session override ?? global
      template ?? default) — so editing the prompt and hitting regenerate does a real re-run
      even on unchanged segments. NULL (legacy rows) reads as not-fresh.
    """
    summary_row = fetch_all(
        conn,
        """
        select 1 from summaries
        where summary_type = 'session' and target_type = 'session'
          and target_id = ? and prompt_version = ?
        limit 1
        """,
        (session_id, PROMPT_VERSION),
    )
    if not summary_row:
        return False
    sidecar_row = fetch_all(
        conn,
        "select source_fingerprint, summary_prompt_fingerprint from session_viewpoint_state where session_id = ?",
        (session_id,),
    )
    if not sidecar_row or sidecar_row[0]["source_fingerprint"] is None:
        return False
    if sidecar_row[0]["summary_prompt_fingerprint"] is None:
        return False
    if str(sidecar_row[0]["summary_prompt_fingerprint"]) != session_prompt_fingerprint(
        config=config, session_id=session_id
    ):
        return False
    stored_fingerprint = str(sidecar_row[0]["source_fingerprint"])
    live_segments = [
        {
            "segment_id": segment["segment_id"],
            "text": segment["text"],
            "speaker": segment.get("speaker"),
            "person_label": segment.get("person_label"),
        }
        for segment in segments
    ]
    return stored_fingerprint == session_fingerprint(live_segments)


def _persist_session_summary(conn: sqlite3.Connection, summary: SessionSummary) -> None:
    now = datetime.now(timezone.utc).isoformat()
    # §37.4 session_summary.v1/v2 are closed schemas; validate before persisting.
    content = validate_session_summary(
        {
            "schema_version": "session_summary.v2",
            **asdict(summary),
        }
    )
    conn.execute(
        """
        insert into summaries (
          summary_id, summary_type, target_type, target_id, prompt_version,
          model_name, content_json, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(summary_type, target_type, target_id, prompt_version) do update set
          model_name = excluded.model_name,
          content_json = excluded.content_json,
          updated_at = excluded.updated_at
        """,
        (
            f"sum_{uuid4().hex}",
            "session",
            "session",
            summary.session_id,
            PROMPT_VERSION,
            "rule_based",
            json.dumps(content, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )


def _validate_summary_evidence_refs(summary: SessionSummary, segments: list[dict[str, object]]) -> None:
    known_refs = {str(segment["evidence_id"]) for segment in segments}
    for decision in summary.decisions:
        if not decision.evidence_refs:
            raise ValueError(f"session decision missing evidence_refs: {decision.text}")
        for evidence_ref in decision.evidence_refs:
            if evidence_ref not in known_refs:
                raise ValueError(f"unknown evidence_id: {evidence_ref}")
    for todo in summary.todos:
        if not todo.evidence_refs:
            raise ValueError(f"session todo missing evidence_refs: {todo.text}")
        for evidence_ref in todo.evidence_refs:
            if evidence_ref not in known_refs:
                raise ValueError(f"unknown evidence_id: {evidence_ref}")


def _generate_session_summary_with_budget(
    *,
    llm: LLMPort,
    session_id: str,
    transcript_segments: list[dict[str, object]],
    max_chunk_tokens: int,
    prompt: str | None = None,
) -> SessionSummary:
    if max_chunk_tokens <= 0 or _segment_tokens(transcript_segments) <= max_chunk_tokens:
        return llm.generate_session_summary(
            session_id=session_id, transcript_segments=transcript_segments, prompt=prompt
        )
    chunks = _segment_chunks(transcript_segments, max_chunk_tokens=max_chunk_tokens)
    chunk_summary_segments: list[dict[str, object]] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_summary = llm.generate_session_summary(
            session_id=f"{session_id}:chunk:{index}",
            transcript_segments=chunk,
            prompt=prompt,
        )
        summary_segment = {
            "segment_id": f"{session_id}_chunk_{index}",
            "start_ms": chunk[0]["start_ms"],
            "end_ms": chunk[-1]["end_ms"],
            "text": chunk_summary.summary,
            "evidence_id": chunk[0]["evidence_id"],
        }
        if "speaker" in chunk[0]:
            summary_segment["speaker"] = "summary"
        chunk_summary_segments.append(summary_segment)
    return llm.generate_session_summary(
        session_id=session_id, transcript_segments=chunk_summary_segments, prompt=prompt
    )


def _segment_chunks(
    transcript_segments: list[dict[str, object]],
    *,
    max_chunk_tokens: int,
) -> list[list[dict[str, object]]]:
    chunks: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_tokens = 0
    for segment in transcript_segments:
        segment_tokens = _text_tokens(str(segment["text"]))
        if current and current_tokens + segment_tokens > max_chunk_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(segment)
        current_tokens += segment_tokens
    if current:
        chunks.append(current)
    return chunks


def _segment_tokens(transcript_segments: list[dict[str, object]]) -> int:
    return sum(_text_tokens(str(segment["text"])) for segment in transcript_segments)


def _text_tokens(text: str) -> int:
    words = text.split()
    return len(words) if words else len(text)


def _llm_segment(row: dict[str, object], *, include_speaker: bool) -> dict[str, object]:
    segment = {
        "segment_id": row["segment_id"],
        "start_ms": row["start_ms"],
        "end_ms": row["end_ms"],
        "text": row["text"],
        "evidence_id": row["evidence_id"],
    }
    if include_speaker:
        segment["speaker"] = row["speaker"]
    return segment
