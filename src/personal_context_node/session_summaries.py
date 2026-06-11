from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import LLMPort, SessionSummary
from personal_context_node.evidence_refs import persist_segment_evidence_refs
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


PROMPT_VERSION = "llm_port.session_summary.v1"


@dataclass(frozen=True)
class SessionSummaryResult:
    summaries_created: int


def summarize_session(*, config: AppConfig, session_id: str, llm: LLMPort) -> SessionSummaryResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        segments = fetch_all(
            conn,
            """
            select segment_id, speaker, start_ms, end_ms, text, evidence_id
            from transcript_segments
            where session_id = ? and is_active = 1
            order by start_ms, segment_id
            """,
            (session_id,),
        )
        if not segments:
            return SessionSummaryResult(summaries_created=0)
        persist_segment_evidence_refs(conn, segments=segments, owner_id=config.owner_did)
        llm_segments = [_llm_segment(row, include_speaker=config.send_speaker_labels) for row in segments]
        summary = _generate_session_summary_with_budget(
            llm=llm,
            session_id=session_id,
            transcript_segments=llm_segments,
            max_chunk_tokens=config.max_chunk_tokens,
        )
        _validate_summary_evidence_refs(summary, segments)
        _persist_session_summary(conn, summary)
        conn.commit()
        return SessionSummaryResult(summaries_created=1)
    finally:
        conn.close()


def _persist_session_summary(conn: sqlite3.Connection, summary: SessionSummary) -> None:
    now = datetime.now(timezone.utc).isoformat()
    content = {
        "schema_version": "session_summary.v1",
        **asdict(summary),
    }
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
        for evidence_ref in decision.evidence_refs:
            if evidence_ref not in known_refs:
                raise ValueError(f"unknown evidence_id: {evidence_ref}")
    for todo in summary.todos:
        for evidence_ref in todo.evidence_refs:
            if evidence_ref not in known_refs:
                raise ValueError(f"unknown evidence_id: {evidence_ref}")


def _generate_session_summary_with_budget(
    *,
    llm: LLMPort,
    session_id: str,
    transcript_segments: list[dict[str, object]],
    max_chunk_tokens: int,
) -> SessionSummary:
    if max_chunk_tokens <= 0 or _segment_tokens(transcript_segments) <= max_chunk_tokens:
        return llm.generate_session_summary(session_id=session_id, transcript_segments=transcript_segments)
    chunks = _segment_chunks(transcript_segments, max_chunk_tokens=max_chunk_tokens)
    chunk_summary_segments: list[dict[str, object]] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk_summary = llm.generate_session_summary(
            session_id=f"{session_id}:chunk:{index}",
            transcript_segments=chunk,
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
    return llm.generate_session_summary(session_id=session_id, transcript_segments=chunk_summary_segments)


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
