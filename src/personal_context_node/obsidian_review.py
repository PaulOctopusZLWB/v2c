from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryCard,
    SubjectRef,
)
from personal_context_node.daily_reports import set_daily_report_status
from personal_context_node.signed_event_store import create_chained_event, insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class ConfirmCandidatesResult:
    candidates_confirmed: int
    signed_events_created: int


def publish_candidate_review(*, config: AppConfig, day: str) -> Path:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select candidate_id, candidate_claim, claim_type, confidence
            from memory_candidates
            where status = 'pending_review'
            order by candidate_id
            """,
        )
    finally:
        conn.close()
    review_dir = config.obsidian_vault / "30_Memory_Candidates"
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = review_dir / f"{day}.md"
    lines = [
        f"# {day} Memory Candidate Review",
        "",
        "<!-- pcn-review-format: memory-candidates.v1 -->",
        "",
    ]
    for row in rows:
        lines.append(f"- [ ] {row['candidate_id']} | {row['claim_type']} | {row['candidate_claim']}")
    review_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    set_daily_report_status(config=config, day=day, status="review_pending")
    return review_path


def confirm_checked_candidates(*, config: AppConfig, day: str) -> ConfirmCandidatesResult:
    review_path = config.obsidian_vault / "30_Memory_Candidates" / f"{day}.md"
    checked_ids = _checked_candidate_ids(review_path)
    if not checked_ids:
        return ConfirmCandidatesResult(candidates_confirmed=0, signed_events_created=0)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        confirmed = 0
        events = 0
        receipts: dict[str, str] = {}
        for candidate_id in checked_ids:
            row = conn.execute(
                """
                select candidate_id, candidate_claim, claim_type, subject_json, evidence_refs_json, status
                from memory_candidates
                where candidate_id = ?
                """,
                (candidate_id,),
            ).fetchone()
            if row is None or row["status"] != "pending_review":
                continue
            card = MemoryCard(
                card_id=f"mem_{uuid4().hex}",
                owner_did=config.owner_did,
                claim_type=row["claim_type"],
                claim=row["candidate_claim"],
                subject=SubjectRef.model_validate(json.loads(row["subject_json"])),
                evidence_refs=[EvidenceRef.model_validate(item) for item in json.loads(row["evidence_refs_json"])],
                candidate_claim=row["candidate_claim"],
            )
            event, public_key = create_chained_event(
                conn,
                event_type="memory_card.created",
                payload=card,
                signer_did=config.owner_did,
            )
            insert_signed_event(conn, event=event, public_key=public_key)
            conn.execute(
                "update memory_candidates set status = 'confirmed', memory_card_id = ? where candidate_id = ?",
                (card.card_id, candidate_id),
            )
            receipts[candidate_id] = card.card_id
            confirmed += 1
            events += 1
        conn.commit()
        if confirmed:
            _rewrite_confirmed_receipts(review_path, receipts)
            set_daily_report_status(config=config, day=day, status="review_synced")
        return ConfirmCandidatesResult(candidates_confirmed=confirmed, signed_events_created=events)
    finally:
        conn.close()


def _checked_candidate_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    receipt_ids = set(re.findall(r'candidate_id="([^"]+)"', path.read_text(encoding="utf-8")))
    checked: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"- \[[xX]\] (cand_[^ |]+) \|", line)
        if match and match.group(1) not in receipt_ids:
            checked.append(match.group(1))
    return checked


def _rewrite_confirmed_receipts(path: Path, receipts: dict[str, str]) -> None:
    synced_at = datetime.now(timezone.utc).isoformat()
    rewritten: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"- \[[xX]\] (cand_[^ |]+) \| ([^|]+) \| (.+)", line)
        if not match or match.group(1) not in receipts:
            rewritten.append(line)
            continue
        candidate_id = match.group(1)
        card_id = receipts[candidate_id]
        rewritten.append(
            f'<!-- pcn:review_receipt start kind="managed" candidate_id="{candidate_id}" '
            f'action=confirm card_id={card_id} synced_at="{synced_at}" -->'
        )
        rewritten.append(
            f"confirmed {candidate_id} -> {card_id}; original_claim={match.group(3)}"
        )
        rewritten.append(f'<!-- pcn:review_receipt end candidate_id="{candidate_id}" -->')
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
