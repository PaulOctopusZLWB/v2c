from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class PublishConfirmedMemoryResult:
    notes_written: int
    note_path: Path


def publish_confirmed_memory_note(*, config: AppConfig, day: str, source_run_id: str | None = None) -> PublishConfirmedMemoryResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select
              active_memory_cards.card_id,
              active_memory_cards.claim_type,
              active_memory_cards.claim,
              active_memory_cards.subject_json,
              active_memory_cards.evidence_refs_json,
              active_memory_cards.confidence,
              active_memory_cards.source_event_hash,
              active_memory_cards.created_at
            from active_memory_cards
            left join memory_candidates
              on memory_candidates.created_card_id = active_memory_cards.card_id
            where memory_candidates.review_note_path like '%' || ? || '.md'
               or (memory_candidates.created_card_id is null and substr(active_memory_cards.created_at, 1, 10) = ?)
            order by active_memory_cards.created_at, active_memory_cards.card_id
            """,
            (day, day),
        )
    finally:
        conn.close()

    output_dir = config.obsidian_vault / "40_Confirmed_Memory"
    output_dir.mkdir(parents=True, exist_ok=True)
    note_path = output_dir / f"{day}.md"
    if not rows:
        return PublishConfirmedMemoryResult(notes_written=0, note_path=note_path)
    note_path.write_text(_confirmed_memory_note_text(day=day, rows=rows, source_run_id=source_run_id), encoding="utf-8")
    return PublishConfirmedMemoryResult(notes_written=1, note_path=note_path)


def _confirmed_memory_note_text(*, day: str, rows: list[dict[str, object]], source_run_id: str | None = None) -> str:
    lines = [
        "---",
        "pcn_schema: markdown_note.v1",
        "note_type: confirmed_memory",
        f"date_key: {day}",
        "generated_by: personal-context-node",
        f"generated_at: {datetime.now(timezone.utc).isoformat()}",
        *([f"source_run_id: {source_run_id}"] if source_run_id else []),
        "pcn_managed: true",
        "---",
        "",
        f"# {day} Confirmed Memory",
        "",
        "## Confirmed Memory",
        "",
    ]
    for row in rows:
        subject = json.loads(str(row["subject_json"]))
        evidence_refs = json.loads(str(row["evidence_refs_json"]))
        lines.extend(
            [
                f'<!-- pcn:managed start type="confirmed_memory_card" card_id="{row["card_id"]}" -->',
                f"- {row['claim']}",
                f"  - card_id: {row['card_id']}",
                f"  - claim_type: {row['claim_type']}",
                f"  - subject: {subject.get('label', subject.get('id', 'unknown'))}",
                f"  - confidence: {row['confidence']}",
                f"  - source_event_hash: {row['source_event_hash']}",
            ]
        )
        for evidence in evidence_refs:
            lines.append(f"  - evidence: {evidence.get('evidence_id')} -> {evidence.get('source_id')}")
        lines.extend(
            [
                f'<!-- pcn:managed end type="confirmed_memory_card" card_id="{row["card_id"]}" -->',
                "",
            ]
        )
    return "\n".join(lines)
