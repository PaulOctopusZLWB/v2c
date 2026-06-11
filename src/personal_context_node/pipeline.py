from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryCard,
    SubjectRef,
)
from personal_context_node.identity_keys import load_or_create_signing_key
from personal_context_node.ingest import import_audio_files_in_conn
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
) -> FirstMilestoneResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        imported = import_audio_files_in_conn(conn, config=config, source_dir=source_dir)
        conn.commit()
        _mock_transcribe(conn)
        _create_memory_candidates(conn)
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


def _mock_transcribe(conn: sqlite3.Connection) -> None:
    rows = fetch_all(
        conn,
        """
        select audio_file_id, local_raw_path
        from audio_files
        where audio_file_id not in (select audio_file_id from transcript_segments)
        order by local_raw_path
        """,
    )
    for row in rows:
        source_name = Path(row["local_raw_path"]).name
        segment_id = f"seg_{uuid4().hex}"
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, start_ms, end_ms, text, language, speaker, evidence_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment_id,
                row["audio_file_id"],
                0,
                3000,
                f"模拟转写：{source_name} 需要生成本地上下文和记忆候选。",
                "zh",
                "self",
                f"ev_{segment_id}",
            ),
        )
    conn.commit()


def _create_memory_candidates(conn: sqlite3.Connection) -> None:
    rows = fetch_all(
        conn,
        """
        select ts.segment_id, ts.evidence_id, ts.text
        from transcript_segments ts
        where ts.evidence_id not in (
          select json_extract(value, '$.evidence_id')
          from memory_candidates, json_each(memory_candidates.evidence_refs_json)
        )
        order by ts.segment_id
        """,
    )
    for row in rows:
        evidence = [
            {
                "evidence_id": row["evidence_id"],
                "source_type": "transcript_segment",
                "source_id": row["segment_id"],
                "quote": row["text"],
            }
        ]
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"cand_{uuid4().hex}",
                "用户正在建设 Personal Context Node 的本地音频上下文系统。",
                "observation",
                json.dumps(
                    {"type": "project", "id": "personal_context_node", "label": "Personal Context Node"},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                0.8,
                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                "pending_review",
                None,
            ),
        )
    conn.commit()


def _confirm_first_candidate(conn: sqlite3.Connection, config: AppConfig) -> None:
    row = conn.execute(
        """
        select candidate_id, candidate_claim, claim_type, subject_json, evidence_refs_json
        from memory_candidates
        where status = 'pending_review'
        order by candidate_id
        limit 1
        """
    ).fetchone()
    if row is None:
        return
    evidence_refs = [EvidenceRef.model_validate(item) for item in json.loads(row["evidence_refs_json"])]
    card = MemoryCard(
        card_id=f"mem_{uuid4().hex}",
        owner_did=config.owner_did,
        claim_type=row["claim_type"],
        claim=row["candidate_claim"],
        subject=SubjectRef.model_validate(json.loads(row["subject_json"])),
        evidence_refs=evidence_refs,
        source_type="confirmed_generated",
        candidate_claim=row["candidate_claim"],
    )
    event, public_key = create_chained_event(
        conn,
        event_type="memory_card.created",
        payload=card,
        signer_did=config.owner_did,
        private_key=load_or_create_signing_key(config),
    )
    insert_signed_event(conn, event=event, public_key=public_key)
    conn.execute(
        "update memory_candidates set status = 'confirmed', memory_card_id = ? where candidate_id = ?",
        (card.card_id, row["candidate_id"]),
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
        left join memory_candidates mc on mc.evidence_refs_json like '%' || ts.segment_id || '%'
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
        note.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"select count(*) from {table}").fetchone()[0])
