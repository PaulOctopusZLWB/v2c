"""In-app 记忆确认闭环 (design handoff Phase 5).

把 obsidian_review 的确认语义搬进应用内:同一套 MemoryCard + Ed25519 链式签名事件
(create_chained_event / insert_signed_event),确认后同步重写 40_Confirmed_Memory/
的当日笔记。与 vault 勾选流互不冲突 — 两边都只消费 status='pending_review' 的行。

状态机(memory_candidates.status):
  pending_review → confirmed(签名,终态:签名事件是 append-only 链,不可撤)
                 → rejected / deferred(可通过 restore 撤回到 pending_review)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import MemoryCard, SubjectRef
from personal_context_node.evidence_refs import hydrate_candidate_evidence_refs
from personal_context_node.identity_keys import effective_owner_did, load_or_create_signing_key
from personal_context_node.obsidian_memory import publish_confirmed_memory_note
from personal_context_node.signed_event_store import create_chained_event, insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass
class ConfirmReceipt:
    candidate_id: str
    card_id: str
    event_type: str
    signature: str
    note_path: str | None


def list_candidates(*, config: AppConfig, limit: int = 200) -> dict[str, object]:
    """Pending-first candidate list (recently reviewed ones trail for the undo flow)."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select candidate_id, date_key, candidate_claim, edited_claim, claim_type,
                   subject_json, confidence, source_type, status, memory_card_id,
                   reviewed_at, evidence_refs_json, created_at
            from memory_candidates
            where status in ('pending_review', 'deferred', 'rejected', 'confirmed')
            order by
              case status when 'pending_review' then 0 when 'deferred' then 1 else 2 end,
              created_at desc
            limit ?
            """,
            (limit,),
        )
        candidates = []
        for row in rows:
            refs = hydrate_candidate_evidence_refs(conn, str(row["evidence_refs_json"]))
            # segment → session 映射(「跳到转写」需要 session_id 打开会话)。
            seg_ids = [r.source_id for r in refs if r.source_type == "transcript_segment"]
            session_by_seg: dict[str, str | None] = {}
            if seg_ids:
                placeholders = ",".join("?" for _ in seg_ids)
                for seg in fetch_all(
                    conn,
                    f"select segment_id, session_id from transcript_segments where segment_id in ({placeholders})",
                    tuple(seg_ids),
                ):
                    session_by_seg[str(seg["segment_id"])] = seg["session_id"]
            evidence = [
                {
                    "evidence_id": ref.evidence_id,
                    "source_type": ref.source_type,
                    # transcript_segment 的 source_id 即 segment_id(播放/跳转用)。
                    "segment_id": ref.source_id if ref.source_type == "transcript_segment" else None,
                    "session_id": session_by_seg.get(ref.source_id) if ref.source_type == "transcript_segment" else None,
                    "quote": ref.quote,
                    "summary": ref.summary,
                }
                for ref in refs
            ]
            candidates.append(
                {
                    "candidate_id": row["candidate_id"],
                    "day": row["date_key"],
                    "claim": row["edited_claim"] or row["candidate_claim"],
                    "candidate_claim": row["candidate_claim"],
                    "claim_type": row["claim_type"],
                    "confidence": row["confidence"],
                    "source_type": row["source_type"],
                    "status": row["status"],
                    "memory_card_id": row["memory_card_id"],
                    "reviewed_at": row["reviewed_at"],
                    "evidence": evidence,
                    "created_at": row["created_at"],
                }
            )
        pending = sum(1 for c in candidates if c["status"] == "pending_review")
    finally:
        conn.close()
    return {
        "did": effective_owner_did(config),
        "pending": pending,
        "total": len(candidates),
        "candidates": candidates,
    }


def confirm_candidate(*, config: AppConfig, candidate_id: str, edited_claim: str | None = None) -> ConfirmReceipt:
    """Confirm + sign one pending candidate (mirrors obsidian_review's confirm branch).

    Raises ValueError when the candidate is unknown / not pending / the claim is invalid.
    """
    if edited_claim is not None and not edited_claim.strip():
        raise ValueError("empty edited claim")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute(
            """
            select candidate_id, date_key, candidate_claim, claim_type, subject_json,
                   confidence, evidence_refs_json, status
            from memory_candidates
            where candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown candidate: {candidate_id}")
        if row["status"] != "pending_review":
            raise ValueError(f"candidate not pending: {candidate_id} ({row['status']})")

        signing_key = load_or_create_signing_key(config)
        owner_did = effective_owner_did(config)
        card = MemoryCard(
            card_id=f"mem_{uuid4().hex}",
            owner_did=owner_did,
            claim_type=row["claim_type"],
            claim=edited_claim or row["candidate_claim"],
            subject=SubjectRef.model_validate(json.loads(row["subject_json"])),
            evidence_refs=hydrate_candidate_evidence_refs(conn, str(row["evidence_refs_json"])),
            source_type="confirmed_generated",
            candidate_claim=row["candidate_claim"],
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            visibility={"type": "private"},
            tags=[],
        )
        event, public_key = create_chained_event(
            conn,
            event_type="memory_card.created",
            payload=card,
            signer_did=owner_did,
            private_key=signing_key,
        )
        insert_signed_event(conn, event=event, public_key=public_key)
        reviewed_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            update memory_candidates
            set status = 'confirmed', memory_card_id = ?, created_card_id = ?,
                edited_claim = ?, reviewed_at = ?, updated_at = ?
            where candidate_id = ?
            """,
            (card.card_id, card.card_id, edited_claim, reviewed_at, reviewed_at, candidate_id),
        )
        conn.commit()
        day = str(row["date_key"]) if row["date_key"] else reviewed_at[:10]
    finally:
        conn.close()

    # 写回 vault(40_Confirmed_Memory/{day}.md);vault 缺失/异常不影响签名结果。
    note_path: str | None = None
    try:
        note_path = str(publish_confirmed_memory_note(config=config, day=day).note_path)
    except Exception:
        note_path = None
    return ConfirmReceipt(
        candidate_id=candidate_id,
        card_id=card.card_id,
        event_type="memory_card.created",
        signature=event.signature.value,
        note_path=note_path,
    )


def _set_status(*, config: AppConfig, candidate_id: str, expect: tuple[str, ...], status: str) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute(
            "select status from memory_candidates where candidate_id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown candidate: {candidate_id}")
        if str(row["status"]) not in expect:
            raise ValueError(f"candidate not in {expect}: {candidate_id} ({row['status']})")
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "update memory_candidates set status = ?, reviewed_at = ?, updated_at = ? where candidate_id = ?",
            (status, now if status != "pending_review" else None, now, candidate_id),
        )
        conn.commit()
    finally:
        conn.close()


def reject_candidate(*, config: AppConfig, candidate_id: str) -> None:
    _set_status(config=config, candidate_id=candidate_id, expect=("pending_review",), status="rejected")


def defer_candidate(*, config: AppConfig, candidate_id: str) -> None:
    _set_status(config=config, candidate_id=candidate_id, expect=("pending_review",), status="deferred")


def restore_candidate(*, config: AppConfig, candidate_id: str) -> None:
    """z 撤销:rejected/deferred → pending_review(confirmed 已签名,不可撤)。"""
    _set_status(
        config=config, candidate_id=candidate_id, expect=("rejected", "deferred"), status="pending_review"
    )
