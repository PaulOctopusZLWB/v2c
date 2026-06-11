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
        segments = fetch_all(
            conn,
            """
            select ts.segment_id, ts.speaker, ts.start_ms, ts.end_ms, ts.text, ts.evidence_id
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            where substr(af.recorded_at, 1, 10) = ?
            order by ts.start_ms
            """,
            (day,),
        )
        if not segments:
            return DailyContextGenerationResult(summaries_created=0, memory_candidates_created=0)
        context = llm.generate_daily_context(day=day, transcript_segments=segments)
        _persist_summary(conn, context)
        candidates_created = _persist_candidates(conn, context=context, segments=segments)
        conn.commit()
        set_daily_report_status(config=config, day=day, status="generated")
        return DailyContextGenerationResult(summaries_created=1, memory_candidates_created=candidates_created)
    finally:
        conn.close()


def _persist_summary(conn: sqlite3.Connection, context: DailyContext) -> None:
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


def _persist_candidates(conn: sqlite3.Connection, *, context: DailyContext, segments: list[dict[str, object]]) -> int:
    segment_by_id = {str(segment["segment_id"]): segment for segment in segments}
    created = 0
    for candidate in context.memory_candidates:
        evidence_refs = []
        for source_id in candidate.evidence_source_ids:
            source = segment_by_id[source_id]
            evidence_refs.append(
                {
                    "evidence_id": source["evidence_id"],
                    "source_type": "transcript_segment",
                    "source_id": source["segment_id"],
                    "quote": source["text"],
                }
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
