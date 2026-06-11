from __future__ import annotations

import json
import time
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from personal_context_node.atomic_write import write_text_atomic
from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    MemoryCard,
    SubjectRef,
)
from personal_context_node.daily_reports import set_daily_report_status
from personal_context_node.evidence_refs import evidence_ids_from_candidate_json, hydrate_candidate_evidence_refs
from personal_context_node.identity_keys import load_or_create_signing_key
from personal_context_node.obsidian_safety import assert_personal_context_vault
from personal_context_node.obsidian_sync_log import record_sync_log
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
    visibility: "ReviewYamlValue | None" = None
    parse_error: str | None = None


ReviewYamlValue = str | dict[str, str]
ALLOWED_REVIEW_ACTIONS = {"confirm", "edit", "reject", "defer", "exclude_from_memory"}


def publish_candidate_review(*, config: AppConfig, day: str, source_run_id: str | None = None) -> Path:
    assert_personal_context_vault(config)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select candidate_id, candidate_claim, claim_type, confidence
            from memory_candidates
            where status = 'pending_review'
              and date_key = ?
            order by candidate_id
            """,
            (day,),
        )
    finally:
        conn.close()
    review_dir = config.obsidian_vault / "30_Memory_Candidates"
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = review_dir / f"{day}.md"
    lines = [
        "---",
        "pcn_schema: markdown_note.v1",
        "note_type: memory_candidate_review",
        f"date_key: {day}",
        "generated_by: personal-context-node",
        f"generated_at: {datetime.now(timezone.utc).isoformat()}",
        *([f"source_run_id: {source_run_id}"] if source_run_id else []),
        "pcn_managed: true",
        "---",
        "",
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
    write_text_atomic(review_path, "\n".join(lines) + "\n")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            update memory_candidates
            set review_note_path = ?, updated_at = ?
            where status = 'pending_review'
              and date_key = ?
            """,
            (str(review_path), now, day),
        )
        conn.commit()
    finally:
        conn.close()
    set_daily_report_status(config=config, day=day, status="review_pending")
    return review_path


def confirm_checked_candidates(*, config: AppConfig, day: str) -> ConfirmCandidatesResult:
    assert_personal_context_vault(config)
    review_path = config.obsidian_vault / "30_Memory_Candidates" / f"{day}.md"
    edited_receipt_candidate_ids = _edited_receipt_candidate_ids(review_path)
    checked_actions = _checked_candidate_actions(review_path)
    if not checked_actions and not edited_receipt_candidate_ids:
        return ConfirmCandidatesResult(candidates_confirmed=0, signed_events_created=0)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        if _within_edit_grace(review_path, edit_grace_seconds=config.edit_grace_seconds):
            record_sync_log(
                config=config,
                conn=conn,
                day=day,
                source="memory_candidate_review",
                target_id=day,
                status="skipped",
                message=f"review file modified within edit grace: {day}",
            )
            conn.commit()
            return ConfirmCandidatesResult(candidates_confirmed=0, signed_events_created=0)
        for candidate_id in edited_receipt_candidate_ids:
            record_sync_log(
                config=config,
                conn=conn,
                day=day,
                source="memory_candidate_review",
                target_id=candidate_id,
                status="ignored",
                message=f"ignored edit to consumed review receipt: {candidate_id}",
            )
        confirmed = 0
        events = 0
        receipts: dict[str, dict[str, str | None]] = {}
        signing_key = load_or_create_signing_key(config)
        for review_action in checked_actions:
            candidate_id = review_action.candidate_id
            action = review_action.action
            if action == "parse_error":
                record_sync_log(
                    config=config,
                    conn=conn,
                    day=day,
                    source="memory_candidate_review",
                    target_id=candidate_id,
                    status="failed",
                    message=f"yaml parse failed: {candidate_id}",
                )
                continue
            if action not in ALLOWED_REVIEW_ACTIONS:
                record_sync_log(
                    config=config,
                    conn=conn,
                    day=day,
                    source="memory_candidate_review",
                    target_id=candidate_id,
                    status="failed",
                    message=f"invalid action {action}: {candidate_id}",
                )
                continue
            if action in {"confirm", "edit"} and review_action.edited_claim is not None and not review_action.edited_claim.strip():
                empty_message = "empty edit claim" if action == "edit" else "empty claim"
                record_sync_log(
                    config=config,
                    conn=conn,
                    day=day,
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
                  and date_key = ?
                """,
                (candidate_id, day),
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
                evidence_refs=hydrate_candidate_evidence_refs(conn, str(row["evidence_refs_json"])),
                source_type="confirmed_generated",
                candidate_claim=row["candidate_claim"],
                confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                visibility=review_action.visibility or {"type": "private"},
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
    legacy_source_ids = [
        str(item["source_id"])
        for item in json.loads(evidence_refs_json)
        if isinstance(item, dict) and item.get("source_type") == "transcript_segment" and item.get("source_id")
    ]
    evidence_ids = evidence_ids_from_candidate_json(evidence_refs_json)
    if not evidence_ids and not legacy_source_ids:
        return
    rows = []
    if evidence_ids:
        evidence_placeholders = ",".join("?" for _ in evidence_ids)
        rows = conn.execute(
            f"""
            select source_id
            from evidence_refs
            where evidence_id in ({evidence_placeholders})
              and source_type = 'transcript_segment'
              and source_id is not null
            """,
            tuple(evidence_ids),
        ).fetchall()
    source_ids = legacy_source_ids + [str(row["source_id"]) for row in rows]
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


def _edited_receipt_candidate_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    receipt_ids = set(re.findall(r'pcn:review_receipt start\b[^>]*candidate_id="([^"]+)"', text))
    edited_ids: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"- \[[xX]\] (cand_[^ |]+) \|", line)
        if match and match.group(1) in receipt_ids:
            edited_ids.add(match.group(1))
    return sorted(edited_ids)


def _review_block_actions(text: str) -> list[ReviewAction]:
    actions: list[ReviewAction] = []
    pattern = re.compile(
        r'<!--\s*pcn:review start\b[^>]*type="memory_candidate"[^>]*candidate_id="(?P<candidate_id>[^"]+)"[^>]*-->'
        r'(?P<body>.*?)'
        r'<!--\s*pcn:review end\b[^>]*candidate_id="(?P=candidate_id)"[^>]*-->',
        flags=re.DOTALL,
    )
    for match in pattern.finditer(text):
        candidate_id = match.group("candidate_id")
        try:
            values = _simple_yaml_block(match.group("body"))
        except ReviewYamlError as error:
            actions.append(ReviewAction(candidate_id, "parse_error", parse_error=str(error)))
            continue
        action = _yaml_scalar(values.get("action", "pending"))
        if action == "pending":
            continue
        edited_claim = _yaml_scalar(values.get("claim", "")) if action in {"confirm", "edit"} else None
        actions.append(ReviewAction(candidate_id, action, edited_claim, values.get("visibility")))
    return actions


class ReviewYamlError(ValueError):
    pass


def _simple_yaml_block(markdown: str) -> dict[str, ReviewYamlValue]:
    fence = re.search(r"```yaml\s*(?P<body>.*?)```", markdown, flags=re.DOTALL)
    body = fence.group("body") if fence else markdown
    try:
        loaded = yaml.safe_load(body)
    except yaml.YAMLError as error:
        raise ReviewYamlError(str(error)) from error
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ReviewYamlError("review yaml must be a mapping")
    return {str(key): _review_yaml_value(value) for key, value in loaded.items()}


def _review_yaml_value(value: object) -> ReviewYamlValue:
    if isinstance(value, dict):
        normalized: dict[str, str] = {}
        for key, nested_value in value.items():
            if isinstance(nested_value, (dict, list)):
                raise ReviewYamlError(f"nested yaml value must be scalar: {key}")
            normalized[str(key)] = "" if nested_value is None else str(nested_value)
        return normalized
    if isinstance(value, list):
        raise ReviewYamlError("review yaml values must be scalar or object")
    return "" if value is None else str(value)


def _yaml_scalar(value: ReviewYamlValue) -> str:
    if isinstance(value, dict):
        return ""
    return value


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
        original_claim = _yaml_scalar(values.get("claim", ""))
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
    write_text_atomic(path, "\n".join(rewritten) + "\n")


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
