from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import DailyContext, LLMPort, MemoryCandidateDraft
from personal_context_node.daily_reports import set_daily_report_status
from personal_context_node.evidence_refs import persist_segment_evidence_refs
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.summary_schemas import validate_daily_summary
from personal_context_node.transcript_review import accepted_segments_clause


@dataclass(frozen=True)
class DailyContextGenerationResult:
    summaries_created: int
    memory_candidates_created: int


def generate_daily_context(*, config: AppConfig, day: str, llm: LLMPort) -> DailyContextGenerationResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        gate = accepted_segments_clause("ts") if config.require_accepted_transcripts else ""
        stored_segments = fetch_all(
            conn,
            f"""
            select ts.segment_id, ts.session_id, ts.speaker, ts.start_ms, ts.end_ms, ts.text, ts.evidence_id
            from transcript_segments ts
            join audio_files af on af.audio_file_id = ts.audio_file_id
            join sessions s on s.session_id = ts.session_id
            where s.date_key = ? and ts.is_active = 1
              and coalesce(s.exclude_from_memory, 0) = 0
              {gate}
            order by s.started_at, ts.start_ms
            """,
            (day,),
        )
        if not stored_segments:
            # An empty day is still a generated (empty) report — never leave the
            # DailyReport state machine stuck in `generating` (§25.3). Still record the
            # §27.1 recording statistics: a day can have recorded audio (total_recorded_ms)
            # with no active/memory-eligible segments (all silent or exclude_from_memory).
            _record_daily_metrics(conn, day=day)
            conn.commit()
            set_daily_report_status(config=config, day=day, status="generated")
            return DailyContextGenerationResult(summaries_created=0, memory_candidates_created=0)
        persist_segment_evidence_refs(conn, segments=stored_segments, owner_id=config.owner_did)
        llm_segments = [_llm_segment(row, include_speaker=config.send_speaker_labels) for row in stored_segments]
        context = llm.generate_daily_context(day=day, transcript_segments=llm_segments)
        _persist_legacy_summary(conn, context)
        _persist_formal_summary(conn, context=context, segments=stored_segments)
        candidates_created = _persist_candidates(
            conn,
            context=context,
            segments=stored_segments,
            owner_id=config.owner_did,
        )
        _record_daily_metrics(conn, day=day)
        conn.commit()
        set_daily_report_status(config=config, day=day, status="generated")
        return DailyContextGenerationResult(summaries_created=1, memory_candidates_created=candidates_created)
    finally:
        conn.close()


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
            json.dumps(_inference_items(context.inferences), ensure_ascii=False, sort_keys=True),
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
    # §37.4 daily_summary.v1 is a closed schema (no `inferences` field); validate it.
    content = validate_daily_summary(
        {
            "schema_version": "daily_summary.v1",
            "date_key": context.day,
            "headline": context.summary,
            "summary": context.summary,
            "highlights": context.facts,
            "decisions_rollup": _decision_rollup(context=context, segments=segments),
            "todos_rollup": _todo_rollup(context=context, segments=segments),
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
    segment_by_llm_ref = _segment_by_llm_ref(segments)
    rollup: list[dict[str, object]] = []
    for candidate in context.memory_candidates:
        if candidate.claim_type != "decision":
            continue
        evidence_refs = []
        session_id: object = None
        for source_id in candidate.evidence_source_ids:
            source = segment_by_llm_ref.get(source_id)
            if source is None:
                raise ValueError(f"unknown evidence_id: {source_id}")
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
        # Daily todos are plain strings (no evidence id); attach the best-matching
        # segment heuristically. A paraphrased todo with no verbatim match yields empty
        # evidence rather than crashing the whole daily report (§37.4 allows empty refs).
        source = _segment_for_text(todo, segments)
        rollup.append(
            {
                "text": todo,
                "owner": "self",
                "session_id": source.get("session_id") if source is not None else None,
                "evidence_refs": [str(source["evidence_id"])] if source is not None else [],
            }
        )
    return rollup


def _inference_items(inferences: list[object]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for inference in inferences:
        if isinstance(inference, dict):
            text = str(inference.get("text") or inference.get("claim") or "").strip()
            inference_type = str(inference.get("type", "inference"))
            if inference_type != "inference":
                raise ValueError(f"LLM inference type must be inference: {inference_type}")
            if "confidence" not in inference:
                raise ValueError("LLM inference missing confidence")
            confidence = float(inference["confidence"])
        else:
            text = str(inference).strip()
            confidence = 0.5
        if not text:
            continue
        items.append({"type": "inference", "text": text, "confidence": confidence})
    return items


def _record_daily_metrics(conn: sqlite3.Connection, *, day: str) -> None:
    # Populate the §27.1 daily_reports statistics columns (the SQLite source of truth),
    # not just the Obsidian note. self/others split uses segment attribution with the
    # default self prior (§6 dev-point 3) for unmapped speaker_cluster_id == 'self'.
    total_recorded_ms = int(
        conn.execute(
            "select coalesce(sum(duration_ms), 0) as ms from audio_files where substr(recorded_at, 1, 10) = ?",
            (day,),
        ).fetchone()["ms"]
    )
    rows = conn.execute(
        """
        select ts.start_ms, ts.end_ms, ts.speaker_cluster_id, attr.person_id, p.is_self
        from transcript_segments ts
        join sessions s on s.session_id = ts.session_id
        left join v_segment_attribution attr on attr.segment_id = ts.segment_id
        left join persons p on p.person_id = attr.person_id
        where s.date_key = ? and ts.is_active = 1
        """,
        (day,),
    ).fetchall()
    active_speech_ms = 0
    self_speech_ms = 0
    for row in rows:
        duration = max(0, int(row["end_ms"]) - int(row["start_ms"]))
        active_speech_ms += duration
        is_self = int(row["is_self"]) == 1 if row["is_self"] is not None else (
            row["person_id"] is None and str(row["speaker_cluster_id"]) == "self"
        )
        if is_self:
            self_speech_ms += duration
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        insert into daily_reports (
          date_key, status, total_recorded_ms, active_speech_ms, self_speech_ms,
          others_speech_ms, generated_at, created_at, updated_at
        ) values (?, 'generating', ?, ?, ?, ?, ?, ?, ?)
        on conflict(date_key) do update set
          total_recorded_ms = excluded.total_recorded_ms,
          active_speech_ms = excluded.active_speech_ms,
          self_speech_ms = excluded.self_speech_ms,
          others_speech_ms = excluded.others_speech_ms,
          generated_at = excluded.generated_at,
          updated_at = excluded.updated_at
        """,
        (
            day,
            total_recorded_ms,
            active_speech_ms,
            self_speech_ms,
            active_speech_ms - self_speech_ms,
            now,
            now,
            now,
        ),
    )


def _segment_for_text(text: str, segments: list[dict[str, object]]) -> dict[str, object] | None:
    # Prefer the shortest segment that contains the rollup fragment — it is the most
    # likely origin (e.g. "需要买面包。" over "其实不需要买面包了") — reducing evidence
    # misattribution in daily todo/decision rollups (deterministic tiebreak by id).
    matches = [segment for segment in segments if text in str(segment["text"])]
    if not matches:
        return None
    return min(matches, key=lambda segment: (len(str(segment["text"])), str(segment["segment_id"])))


def _persist_candidates(
    conn: sqlite3.Connection,
    *,
    context: DailyContext,
    segments: list[dict[str, object]],
    owner_id: str,
) -> int:
    segment_by_llm_ref = _segment_by_llm_ref(segments)
    created = 0
    for candidate in _merge_daily_duplicate_candidates(context.memory_candidates):
        evidence_refs = []
        for source_id in candidate.evidence_source_ids:
            source = segment_by_llm_ref.get(source_id)
            if source is None:
                raise ValueError(f"unknown evidence_id: {source_id}")
            evidence_refs.append(str(source["evidence_id"]))
        if not evidence_refs:
            raise ValueError("LLM memory candidates require evidence refs")
        normalized_claim_hash = _normalized_claim_hash(candidate.candidate_claim)
        subject_json = _subject_json(candidate.subject)
        subject_id = _candidate_subject(candidate.subject)["id"]
        now = datetime.now(timezone.utc).isoformat()
        # Dedup key is subject.id + claim_type + normalized_claim_hash (§37.2.1) — match on
        # the subject id, not the full subject_json (which also carries the label).
        existing = conn.execute(
            """
            select candidate_id, evidence_refs_json
            from memory_candidates
            where date_key = ? and claim_type = ? and normalized_claim_hash = ?
              and json_extract(subject_json, '$.id') = ?
            limit 1
            """,
            (context.day, candidate.claim_type, normalized_claim_hash, subject_id),
        ).fetchone()
        if existing is not None:
            # Same-day duplicate (dedup key = subject.id + claim_type + claim hash,
            # §37.2): merge evidence into the existing candidate instead of inserting a
            # new row. This keeps daily_generate re-runs idempotent and never
            # re-presents an already-confirmed/rejected claim for review.
            merged_refs = _unique_preserve_order(
                [str(item) for item in json.loads(str(existing["evidence_refs_json"]))] + evidence_refs
            )
            conn.execute(
                "update memory_candidates set evidence_refs_json = ?, updated_at = ? where candidate_id = ?",
                (json.dumps(merged_refs, ensure_ascii=False, sort_keys=True), now, existing["candidate_id"]),
            )
            continue
        status = _candidate_status_for_daily_duplicate(
            conn,
            day=context.day,
            subject_id=subject_id,
            claim_type=candidate.claim_type,
            normalized_claim_hash=normalized_claim_hash,
        )
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, source_type, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id,
              date_key, normalized_claim_hash, prompt_version, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"cand_{uuid4().hex}",
                "llm_daily_context",
                candidate.candidate_claim,
                candidate.claim_type,
                subject_json,
                candidate.confidence,
                json.dumps(evidence_refs, ensure_ascii=False, sort_keys=True),
                status,
                None,
                context.day,
                normalized_claim_hash,
                "llm_port.candidate_extraction.v1",
                now,
                now,
            ),
        )
        created += 1
    return created


def _segment_by_llm_ref(segments: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(segment["evidence_id"]): segment for segment in segments}


def _merge_daily_duplicate_candidates(candidates: list[MemoryCandidateDraft]) -> list[MemoryCandidateDraft]:
    merged: dict[tuple[str, str, str], MemoryCandidateDraft] = {}
    for candidate in candidates:
        normalized_claim = _normalize_claim(candidate.candidate_claim)
        subject = _candidate_subject(candidate.subject)
        key = (subject["id"], candidate.claim_type, normalized_claim)
        existing = merged.get(key)
        evidence_source_ids = _unique_preserve_order(
            (existing.evidence_source_ids if existing else []) + candidate.evidence_source_ids
        )
        merged[key] = MemoryCandidateDraft(
            candidate_claim=existing.candidate_claim if existing else candidate.candidate_claim.strip(),
            claim_type=candidate.claim_type,
            confidence=max(existing.confidence if existing else candidate.confidence, candidate.confidence),
            evidence_source_ids=evidence_source_ids,
            subject=subject,
        )
    return list(merged.values())


def _normalize_claim(value: str) -> str:
    return " ".join(value.split()).casefold()


def _normalized_claim_hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(_normalize_claim(value).encode('utf-8')).hexdigest()}"


def _candidate_subject(subject: dict[str, str] | None) -> dict[str, str]:
    if not subject:
        return {"type": "project", "id": "personal_context_node", "label": "Personal Context Node"}
    for field in ["type", "id", "label"]:
        if not str(subject.get(field, "")).strip():
            raise ValueError(f"LLM memory candidate subject missing {field}")
    return {"type": str(subject["type"]), "id": str(subject["id"]), "label": str(subject["label"])}


def _subject_json(subject: dict[str, str]) -> str:
    return json.dumps(_candidate_subject(subject), ensure_ascii=False, sort_keys=True)


def _candidate_status_for_daily_duplicate(
    conn: sqlite3.Connection,
    *,
    day: str,
    subject_id: str,
    claim_type: str,
    normalized_claim_hash: str,
) -> str:
    # Cross-day duplicate by the §37.2.1 key (subject.id + claim_type + claim hash).
    duplicate = conn.execute(
        """
        select 1
        from memory_candidates
        where claim_type = ?
          and normalized_claim_hash = ?
          and json_extract(subject_json, '$.id') = ?
          and date_key is not null
          and date_key <> ?
        limit 1
        """,
        (claim_type, normalized_claim_hash, subject_id, day),
    ).fetchone()
    return "possible_duplicate" if duplicate else "pending_review"


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
