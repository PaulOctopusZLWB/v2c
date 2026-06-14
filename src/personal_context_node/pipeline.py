from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.core.ports.llm import LLMPort
from personal_context_node.core.protocols.memory import (
    MemoryCard,
    SubjectRef,
)
from personal_context_node.evidence_refs import hydrate_candidate_evidence_refs
from personal_context_node.identity_keys import effective_owner_did, load_or_create_signing_key
from personal_context_node.ingest import import_audio_files_in_conn
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.sessions import derive_sessions_for_day
from personal_context_node.signed_event_store import create_chained_event, insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class FirstMilestoneResult:
    imported_files: int
    transcript_segments: int
    memory_candidates: int
    signed_events: int


def run_first_milestone(
    *,
    config: AppConfig,
    source_dir: Path,
    confirm_first_candidate: bool = False,
    llm: LLMPort | None = None,
) -> FirstMilestoneResult:
    llm_adapter = llm or RuleBasedLLMAdapter()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        imported = import_audio_files_in_conn(conn, config=config, source_dir=source_dir)
        conn.commit()
        _mock_transcribe(conn)
        days = _transcript_days(conn)
        for day in days:
            derive_sessions_for_day(config=config, day=day)
            generate_daily_context(config=config, day=day, llm=llm_adapter)
        if confirm_first_candidate:
            _confirm_first_candidate(conn, config)
        _publish_daily_notes(conn, config)
        return FirstMilestoneResult(
            imported_files=imported,
            transcript_segments=_count(conn, "transcript_segments"),
            memory_candidates=_count(conn, "memory_candidates"),
            signed_events=_count(conn, "signed_events"),
        )
    finally:
        conn.close()


def _transcript_days(conn: sqlite3.Connection) -> list[str]:
    rows = fetch_all(
        conn,
        """
        select distinct substr(af.recorded_at, 1, 10) as date_key
        from transcript_segments ts
        join audio_files af on af.audio_file_id = ts.audio_file_id
        order by date_key
        """,
    )
    return [str(row["date_key"]) for row in rows]


def _mock_transcribe(conn: sqlite3.Connection) -> None:
    fixture = _mock_transcript_fixture()
    segment_fixture = fixture["segments"][0]
    rows = fetch_all(
        conn,
        """
        select audio_file_id, local_raw_path, recorded_at
        from audio_files
        where audio_file_id not in (select audio_file_id from transcript_segments)
        order by local_raw_path
        """,
    )
    for index, row in enumerate(rows, start=1):
        segment_id = f"seg_{uuid4().hex}"
        chunk_id = f"chk_{segment_id}"
        start_ms = int(segment_fixture["start_ms"])
        end_ms = int(segment_fixture["end_ms"])
        absolute_start_at = _absolute_time(str(row["recorded_at"]), start_ms)
        absolute_end_at = _absolute_time(str(row["recorded_at"]), end_ms)
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, start_ms, end_ms,
              absolute_start_at, absolute_end_at, text, language, speaker, evidence_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment_id,
                row["audio_file_id"],
                chunk_id,
                start_ms,
                end_ms,
                absolute_start_at,
                absolute_end_at,
                str(segment_fixture["text_template"]).format(index=index),
                str(segment_fixture["language"]),
                str(segment_fixture["speaker"]),
                f"ev_{segment_id}",
            ),
        )
    conn.commit()


def _mock_transcript_fixture() -> dict[str, object]:
    fixture_path = files("personal_context_node").joinpath("fixtures/mock_first_milestone_transcript.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _absolute_time(recorded_at: str, offset_ms: int) -> str:
    return (datetime.fromisoformat(recorded_at) + timedelta(milliseconds=offset_ms)).isoformat()


def _create_memory_candidates(conn: sqlite3.Connection) -> None:
    rows = fetch_all(
        conn,
        """
        select ts.segment_id, ts.evidence_id, ts.text, substr(af.recorded_at, 1, 10) as date_key
        from transcript_segments ts
        join audio_files af on af.audio_file_id = ts.audio_file_id
        where ts.evidence_id not in (
          select coalesce(json_extract(value, '$.evidence_id'), value)
          from memory_candidates, json_each(memory_candidates.evidence_refs_json)
        )
        order by ts.segment_id
        """,
    )
    for row in rows:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            insert into evidence_refs (
              evidence_id, source_type, source_ref, source_id, quote, created_at
            ) values (?, ?, ?, ?, ?, ?)
            on conflict(evidence_id) do update set
              source_type = excluded.source_type,
              source_ref = excluded.source_ref,
              source_id = excluded.source_id,
              quote = excluded.quote
            """,
            (
                row["evidence_id"],
                "transcript_segment",
                row["segment_id"],
                row["segment_id"],
                row["text"],
                now,
            ),
        )
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, source_type, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id, date_key,
              prompt_version, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"cand_{uuid4().hex}",
                "mock_first_milestone",
                "用户正在建设 Personal Context Node 的本地音频上下文系统。",
                "observation",
                json.dumps(
                    {"type": "project", "id": "personal_context_node", "label": "Personal Context Node"},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                0.8,
                json.dumps([row["evidence_id"]], ensure_ascii=False, sort_keys=True),
                "pending_review",
                None,
                row["date_key"],
                "llm_port.candidate_extraction.v1",
                now,
                now,
            ),
        )
    conn.commit()


def _confirm_first_candidate(conn: sqlite3.Connection, config: AppConfig) -> None:
    row = conn.execute(
        """
        select candidate_id, candidate_claim, claim_type, subject_json, confidence, evidence_refs_json
        from memory_candidates
        where status = 'pending_review'
        order by candidate_id
        limit 1
        """
    ).fetchone()
    if row is None:
        return
    evidence_refs = hydrate_candidate_evidence_refs(conn, str(row["evidence_refs_json"]))
    owner_did = effective_owner_did(config)
    card = MemoryCard(
        card_id=f"mem_{uuid4().hex}",
        owner_did=owner_did,
        claim_type=row["claim_type"],
        claim=row["candidate_claim"],
        subject=SubjectRef.model_validate(json.loads(row["subject_json"])),
        evidence_refs=evidence_refs,
        source_type="confirmed_generated",
        candidate_claim=row["candidate_claim"],
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
    )
    event, public_key = create_chained_event(
        conn,
        event_type="memory_card.created",
        payload=card,
        signer_did=owner_did,
        private_key=load_or_create_signing_key(config),
    )
    insert_signed_event(conn, event=event, public_key=public_key)
    reviewed_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        update memory_candidates
        set status = 'confirmed',
            memory_card_id = ?,
            created_card_id = ?,
            reviewed_at = ?,
            updated_at = ?
        where candidate_id = ?
        """,
        (card.card_id, card.card_id, reviewed_at, reviewed_at, row["candidate_id"]),
    )
    conn.commit()


def _publish_daily_notes(conn: sqlite3.Connection, config: AppConfig) -> None:
    for folder in ["00_Inbox", "10_Daily", "20_Conversations", "30_Memory_Candidates", "40_Confirmed_Memory", "90_System"]:
        (config.obsidian_vault / folder).mkdir(parents=True, exist_ok=True)

    rows = fetch_all(
        conn,
        """
        select af.local_raw_path, af.recorded_at, ts.text, ts.speaker, mc.candidate_claim, mc.status
        from audio_files af
        join transcript_segments ts on ts.audio_file_id = af.audio_file_id
        left join memory_candidates mc on mc.evidence_refs_json like '%' || ts.evidence_id || '%'
        order by af.recorded_at, af.local_raw_path
        """,
    )
    by_day: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_day.setdefault(row["recorded_at"][:10], []).append(row)

    for day, day_rows in by_day.items():
        note = config.obsidian_vault / "10_Daily" / f"{day}.md"
        lines = [
            f"# {day} Daily Context",
            "",
            "## Metrics",
            f"- Total imported files: {len({row['local_raw_path'] for row in day_rows})}",
            f"- Transcript segments: {len(day_rows)}",
            "",
            "## Transcript",
        ]
        for row in day_rows:
            lines.append(f"- `{Path(row['local_raw_path']).name}` [{row['speaker']}]: {row['text']}")
        lines.extend(["", "## Memory Candidates"])
        for row in day_rows:
            if row["candidate_claim"]:
                lines.append(f"- [{row['status']}] {row['candidate_claim']}")
        write_text_atomic(note, "\n".join(lines) + "\n")


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"select count(*) from {table}").fetchone()[0])
