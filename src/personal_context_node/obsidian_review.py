from __future__ import annotations

import json
import time
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
from personal_context_node.identity_keys import load_or_create_signing_key
from personal_context_node.signed_event_store import create_chained_event, insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class ConfirmCandidatesResult:
    candidates_confirmed: int
    signed_events_created: int


@dataclass(frozen=True)
class ReviewAction:
    candidate_id: str
    action: str
    edited_claim: str | None = None


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
        lines.extend(
            [
                "",
                f'<!-- pcn:review start type="memory_candidate" candidate_id="{row["candidate_id"]}" version="1" -->',
                "```yaml",
                "action: pending",
                f'claim: "{_yaml_quote(str(row["candidate_claim"]))}"',
                f"claim_type: {row['claim_type']}",
                "```",
                f'<!-- pcn:review end candidate_id="{row["candidate_id"]}" -->',
                "",
            ]
        )
    review_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            update memory_candidates
            set review_note_path = ?, updated_at = ?
            where status = 'pending_review'
            """,
            (str(review_path), now),
        )
        conn.commit()
    finally:
        conn.close()
    set_daily_report_status(config=config, day=day, status="review_pending")
    return review_path


def confirm_checked_candidates(*, config: AppConfig, day: str) -> ConfirmCandidatesResult:
    review_path = config.obsidian_vault / "30_Memory_Candidates" / f"{day}.md"
    checked_actions = _checked_candidate_actions(review_path)
    if not checked_actions:
        return ConfirmCandidatesResult(candidates_confirmed=0, signed_events_created=0)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        if _within_edit_grace(review_path, edit_grace_seconds=config.edit_grace_seconds):
            _insert_sync_log(
                conn,
                source="memory_candidate_review",
                target_id=day,
                status="skipped",
                message=f"review file modified within edit grace: {day}",
            )
            conn.commit()
            return ConfirmCandidatesResult(candidates_confirmed=0, signed_events_created=0)
        confirmed = 0
        events = 0
        receipts: dict[str, dict[str, str | None]] = {}
        signing_key = load_or_create_signing_key(config)
        for review_action in checked_actions:
            candidate_id = review_action.candidate_id
            action = review_action.action
            if action in {"confirm", "edit"} and review_action.edited_claim is not None and not review_action.edited_claim.strip():
                empty_message = "empty edit claim" if action == "edit" else "empty claim"
                _insert_sync_log(
                    conn,
                    source="memory_candidate_review",
                    target_id=candidate_id,
                    status="failed",
                    message=f"{empty_message}: {candidate_id}",
                )
                continue
            row = conn.execute(
                """
                select candidate_id, candidate_claim, claim_type, subject_json, confidence, evidence_refs_json, status
                from memory_candidates
                where candidate_id = ?
                """,
                (candidate_id,),
            ).fetchone()
            if row is None or row["status"] != "pending_review":
                continue
            reviewed_at = datetime.now(timezone.utc).isoformat()
            if action == "reject":
                conn.execute(
                    "update memory_candidates set status = 'rejected', reviewed_at = ?, updated_at = ? where candidate_id = ?",
                    (reviewed_at, reviewed_at, candidate_id),
                )
                receipts[candidate_id] = {"action": "reject", "card_id": None}
                continue
            if action == "defer":
                receipts[candidate_id] = {"action": "defer", "card_id": None}
                continue
            if action == "exclude_from_memory":
                _mark_evidence_sessions_excluded(conn, evidence_refs_json=str(row["evidence_refs_json"]))
                conn.execute(
                    """
                    update memory_candidates
                    set status = 'excluded_from_memory',
                        reviewed_at = ?,
                        updated_at = ?
                    where candidate_id = ?
                    """,
                    (reviewed_at, reviewed_at, candidate_id),
                )
                receipts[candidate_id] = {"action": "exclude_from_memory", "card_id": None}
                continue
            card = MemoryCard(
                card_id=f"mem_{uuid4().hex}",
                owner_did=config.owner_did,
                claim_type=row["claim_type"],
                claim=review_action.edited_claim or row["candidate_claim"],
                subject=SubjectRef.model_validate(json.loads(row["subject_json"])),
                evidence_refs=[EvidenceRef.model_validate(item) for item in json.loads(row["evidence_refs_json"])],
                source_type="confirmed_generated",
                candidate_claim=row["candidate_claim"],
                confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            )
            event, public_key = create_chained_event(
                conn,
                event_type="memory_card.created",
                payload=card,
                signer_did=config.owner_did,
                private_key=signing_key,
            )
            insert_signed_event(conn, event=event, public_key=public_key)
            conn.execute(
                """
                update memory_candidates
                set status = 'confirmed',
                    memory_card_id = ?,
                    created_card_id = ?,
                    edited_claim = ?,
                    reviewed_at = ?,
                    updated_at = ?
                where candidate_id = ?
                """,
                (
                    card.card_id,
                    card.card_id,
                    review_action.edited_claim,
                    reviewed_at,
                    reviewed_at,
                    candidate_id,
                ),
            )
            receipts[candidate_id] = {"action": action, "card_id": card.card_id}
            confirmed += 1
            events += 1
        conn.commit()
        if receipts:
            _rewrite_confirmed_receipts(review_path, receipts)
        if confirmed:
            set_daily_report_status(config=config, day=day, status="review_synced")
        return ConfirmCandidatesResult(candidates_confirmed=confirmed, signed_events_created=events)
    finally:
        conn.close()


def _within_edit_grace(path: Path, *, edit_grace_seconds: int) -> bool:
    if edit_grace_seconds <= 0:
        return False
    return time.time() - path.stat().st_mtime < edit_grace_seconds


def _yaml_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _mark_evidence_sessions_excluded(conn, *, evidence_refs_json: str) -> None:
    source_ids = [
        str(item["source_id"])
        for item in json.loads(evidence_refs_json)
        if item.get("source_type") == "transcript_segment" and item.get("source_id")
    ]
    if not source_ids:
        return
    placeholders = ",".join("?" for _ in source_ids)
    conn.execute(
        f"""
        update sessions
        set exclude_from_memory = 1,
            updated_at = ?
        where session_id in (
          select session_id
          from transcript_segments
          where segment_id in ({placeholders})
            and session_id is not null
        )
        """,
        (datetime.now(timezone.utc).isoformat(), *source_ids),
    )


def _checked_candidate_actions(path: Path) -> list[ReviewAction]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    block_actions = _review_block_actions(text)
    if block_actions:
        return block_actions
    receipt_ids = set(re.findall(r'pcn:review_receipt start\b[^>]*candidate_id="([^"]+)"', text))
    checked: list[ReviewAction] = []
    for line in text.splitlines():
        match = re.match(r"- \[[xX]\] (cand_[^ |]+) \|[^|]+\|[^|]+(?:\| *(.*))?", line)
        if not match or match.group(1) in receipt_ids:
            continue
        action_text = (match.group(2) or "confirm").strip()
        if action_text.startswith("edit:"):
            checked.append(ReviewAction(match.group(1), "edit", action_text.removeprefix("edit:").strip()))
        else:
            checked.append(ReviewAction(match.group(1), action_text or "confirm"))
    return checked


def _review_block_actions(text: str) -> list[ReviewAction]:
    actions: list[ReviewAction] = []
    pattern = re.compile(
        r'<!--\s*pcn:review start\b[^>]*type="memory_candidate"[^>]*candidate_id="(?P<candidate_id>[^"]+)"[^>]*-->'
        r'(?P<body>.*?)'
        r'<!--\s*pcn:review end\b[^>]*candidate_id="(?P=candidate_id)"[^>]*-->',
        flags=re.DOTALL,
    )
    for match in pattern.finditer(text):
        values = _simple_yaml_block(match.group("body"))
        action = values.get("action", "pending")
        if action == "pending":
            continue
        edited_claim = values.get("claim", "") if action in {"confirm", "edit"} else None
        actions.append(ReviewAction(match.group("candidate_id"), action, edited_claim))
    return actions


def _simple_yaml_block(markdown: str) -> dict[str, str]:
    fence = re.search(r"```yaml\s*(?P<body>.*?)```", markdown, flags=re.DOTALL)
    body = fence.group("body") if fence else markdown
    values: dict[str, str] = {}
    for line in body.splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def _insert_sync_log(conn, *, source: str, target_id: str, status: str, message: str) -> None:
    conn.execute(
        """
        insert into sync_logs (sync_log_id, source, target_id, status, message, created_at)
        values (?, ?, ?, ?, ?, ?)
        """,
        (f"sync_{uuid4().hex}", source, target_id, status, message, datetime.now(timezone.utc).isoformat()),
    )


def _rewrite_confirmed_receipts(path: Path, receipts: dict[str, dict[str, str | None]]) -> None:
    synced_at = datetime.now(timezone.utc).isoformat()
    rendered_receipts: set[str] = set()
    text = path.read_text(encoding="utf-8")

    def replace_review_block(match: re.Match[str]) -> str:
        candidate_id = match.group("candidate_id")
        receipt = receipts.get(candidate_id)
        if receipt is None:
            return match.group(0)
        rendered_receipts.add(candidate_id)
        values = _simple_yaml_block(match.group("body"))
        original_claim = values.get("claim", "")
        return "\n".join(_receipt_lines(candidate_id, receipt, original_claim=original_claim, synced_at=synced_at))

    text = re.sub(
        r'<!--\s*pcn:review start\b[^>]*type="memory_candidate"[^>]*candidate_id="(?P<candidate_id>[^"]+)"[^>]*-->'
        r'(?P<body>.*?)'
        r'<!--\s*pcn:review end\b[^>]*candidate_id="(?P=candidate_id)"[^>]*-->',
        replace_review_block,
        text,
        flags=re.DOTALL,
    )

    rewritten: list[str] = []
    for line in text.splitlines():
        match = re.match(r"- \[[ xX]\] (cand_[^ |]+) \| ([^|]+) \| ([^|]+)(?:\| *(.*))?", line)
        if not match or match.group(1) not in receipts:
            rewritten.append(line)
            continue
        candidate_id = match.group(1)
        if candidate_id in rendered_receipts:
            continue
        receipt = receipts[candidate_id]
        rewritten.extend(_receipt_lines(candidate_id, receipt, original_claim=match.group(3), synced_at=synced_at))
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def _receipt_lines(
    candidate_id: str,
    receipt: dict[str, str | None],
    *,
    original_claim: str,
    synced_at: str,
) -> list[str]:
    action = receipt["action"]
    card_id = receipt["card_id"]
    card_part = f" card_id={card_id}" if card_id else ""
    lines = [
        f'<!-- pcn:review_receipt start kind="managed" candidate_id="{candidate_id}" '
        f'action={action}{card_part} synced_at="{synced_at}" -->'
    ]
    if card_id:
        lines.append(f"confirmed {candidate_id} -> {card_id}; original_claim={original_claim}")
    else:
        lines.append(f"{action} {candidate_id}; original_claim={original_claim}")
    lines.append(f'<!-- pcn:review_receipt end candidate_id="{candidate_id}" -->')
    return lines
