from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import DailyContext, LLMPort
from personal_context_node.daily_reports import set_daily_report_status
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class DailyContextGenerationResult:
    summaries_created: int
    memory_candidates_created: int


def generate_daily_context(*, config: AppConfig, day: str, llm: LLMPort) -> DailyContextGenerationResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        stored_segments = fetch_all(
            conn,
            """
            select ts.segment_id, ts.session_id, ts.speaker, ts.start_ms, ts.end_ms, ts.text, ts.evidence_id
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            where substr(af.recorded_at, 1, 10) = ? and ts.is_active = 1
            order by ts.start_ms
            """,
            (day,),
        )
        if not stored_segments:
            return DailyContextGenerationResult(summaries_created=0, memory_candidates_created=0)
        llm_segments = [_llm_segment(row) for row in stored_segments]
        context = llm.generate_daily_context(day=day, transcript_segments=llm_segments)
        _persist_legacy_summary(conn, context)
        _persist_formal_summary(conn, context=context, segments=stored_segments)
        candidates_created = _persist_candidates(conn, context=context, segments=stored_segments)
        conn.commit()
        set_daily_report_status(config=config, day=day, status="generated")
        return DailyContextGenerationResult(summaries_created=1, memory_candidates_created=candidates_created)
    finally:
        conn.close()


def _llm_segment(row: dict[str, object]) -> dict[str, object]:
    return {
        "segment_id": row["segment_id"],
        "speaker": row["speaker"],
        "start_ms": row["start_ms"],
        "end_ms": row["end_ms"],
        "text": row["text"],
        "evidence_id": row["evidence_id"],
    }


def _persist_legacy_summary(conn: sqlite3.Connection, context: DailyContext) -> None:
    conn.execute(
        """
        insert into daily_summaries (
          day, summary, todos_json, facts_json, inferences_json, prompt_version, created_at
        ) values (?, ?, ?, ?, ?, ?, ?)
        on conflict(day) do update set
          summary = excluded.summary,
          todos_json = excluded.todos_json,
          facts_json = excluded.facts_json,
          inferences_json = excluded.inferences_json,
          prompt_version = excluded.prompt_version,
          created_at = excluded.created_at
        """,
        (
            context.day,
            context.summary,
            json.dumps(context.todos, ensure_ascii=False, sort_keys=True),
            json.dumps(context.facts, ensure_ascii=False, sort_keys=True),
            json.dumps(context.inferences, ensure_ascii=False, sort_keys=True),
            "llm_port.daily_context.v1",
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _persist_formal_summary(
    conn: sqlite3.Connection,
    *,
    context: DailyContext,
    segments: list[dict[str, object]],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    content = {
        "schema_version": "daily_summary.v1",
        "date_key": context.day,
        "headline": context.summary,
        "summary": context.summary,
        "highlights": context.facts,
        "decisions_rollup": _decision_rollup(context=context, segments=segments),
        "todos_rollup": _todo_rollup(context=context, segments=segments),
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
            "daily",
            "date_key",
            context.day,
            "llm_port.daily_summary.v1",
            "llm_port",
            json.dumps(content, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )


def _decision_rollup(*, context: DailyContext, segments: list[dict[str, object]]) -> list[dict[str, object]]:
    segment_by_id = {str(segment["segment_id"]): segment for segment in segments}
    rollup: list[dict[str, object]] = []
    for candidate in context.memory_candidates:
        if candidate.claim_type != "decision":
            continue
        evidence_refs = []
        session_id: object = None
        for source_id in candidate.evidence_source_ids:
            source = segment_by_id[source_id]
            evidence_refs.append(str(source["evidence_id"]))
            session_id = source.get("session_id")
        rollup.append(
            {
                "text": candidate.candidate_claim,
                "session_id": session_id,
                "evidence_refs": evidence_refs,
            }
        )
    return rollup


def _todo_rollup(*, context: DailyContext, segments: list[dict[str, object]]) -> list[dict[str, object]]:
    if not context.todos:
        return []
    rollup: list[dict[str, object]] = []
    for todo in context.todos:
        source = _segment_for_text(todo, segments)
        rollup.append(
            {
                "text": todo,
                "owner": "self",
                "session_id": source.get("session_id"),
                "evidence_refs": [str(source["evidence_id"])],
            }
        )
    return rollup


def _segment_for_text(text: str, segments: list[dict[str, object]]) -> dict[str, object]:
    for segment in segments:
        if text in str(segment["text"]):
            return segment
    return segments[0]


def _persist_candidates(conn: sqlite3.Connection, *, context: DailyContext, segments: list[dict[str, object]]) -> int:
    segment_by_llm_ref = {
        ref: segment
        for segment in segments
        for ref in (str(segment["segment_id"]), str(segment["evidence_id"]))
    }
    created = 0
    for candidate in context.memory_candidates:
        evidence_refs = []
        for source_id in candidate.evidence_source_ids:
            source = segment_by_llm_ref.get(source_id)
            if source is None:
                raise ValueError(f"unknown evidence_id: {source_id}")
            evidence_refs.append(
                {
                    "evidence_id": source["evidence_id"],
                    "source_type": "transcript_segment",
                    "source_id": source["segment_id"],
                    "quote": source["text"],
                }
            )
            conn.execute(
                """
                insert into evidence_refs (evidence_id, source_type, source_id, quote)
                values (?, ?, ?, ?)
                on conflict(evidence_id) do update set
                  source_type = excluded.source_type,
                  source_id = excluded.source_id,
                  quote = excluded.quote
                """,
                (source["evidence_id"], "transcript_segment", source["segment_id"], source["text"]),
            )
        if not evidence_refs:
            raise ValueError("LLM memory candidates require evidence refs")
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"cand_{uuid4().hex}",
                candidate.candidate_claim,
                candidate.claim_type,
                json.dumps(
                    {"type": "project", "id": "personal_context_node", "label": "Personal Context Node"},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                candidate.confidence,
                json.dumps(evidence_refs, ensure_ascii=False, sort_keys=True),
                "pending_review",
                None,
            ),
        )
        created += 1
    return created
