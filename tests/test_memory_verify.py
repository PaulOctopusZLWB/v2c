from __future__ import annotations

import json
import os
import time
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import EvidenceRef, MemoryCard, SubjectRef, create_signed_event, signing_body
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_verify_rechecks_stored_signed_events(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 1
    assert result.invalid_events == 0
    assert result.materialization_mismatches == 0


def test_confirmed_candidate_materializes_memory_card(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)

    confirm_checked_candidates(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        cards = fetch_all(
            conn,
            "select claim, claim_type, current_version, source_type, confidence, status, source_event_hash from memory_cards",
        )
    finally:
        conn.close()

    assert cards == [
        {
            "claim": "用户要求音频本地处理。",
            "claim_type": "requirement",
            "current_version": 1,
            "source_type": "confirmed_generated",
            "confidence": 0.95,
            "status": "active",
            "source_event_hash": cards[0]["source_event_hash"],
        }
    ]
    assert cards[0]["source_event_hash"].startswith("sha256:")


def test_memory_card_temporal_bounds_materialize_from_signed_event(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    card = MemoryCard(
        card_id="mem_temporal_test",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Memory cards carry temporal bounds.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_temporal_test",
                source_type="transcript_segment",
                source_id="seg_temporal_test",
                quote="Memory cards carry temporal bounds.",
            )
        ],
        observed_at="2087-05-10T09:30:00Z",
        valid_from="2087-05-10",
        valid_until="2087-06-10",
    )
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )
    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=event, public_key=public_key)
        rows = fetch_all(
            conn,
            "select observed_at, valid_from, valid_until from memory_cards where card_id = ?",
            (card.card_id,),
        )
    finally:
        conn.close()

    assert rows == [
        {
            "observed_at": "2087-05-10T09:30:00Z",
            "valid_from": "2087-05-10",
            "valid_until": "2087-06-10",
        }
    ]


def test_memory_card_updated_at_materializes_from_signed_event_payload(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    card = MemoryCard(
        card_id="mem_updated_at_test",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Memory cards carry updated_at.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_updated_at_test",
                source_type="transcript_segment",
                source_id="seg_updated_at_test",
                quote="Memory cards carry updated_at.",
            )
        ],
        updated_at="2087-05-11T10:00:00Z",
    )
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )
    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=event, public_key=public_key)
        rows = fetch_all(
            conn,
            "select updated_at from memory_cards where card_id = ?",
            (card.card_id,),
        )
    finally:
        conn.close()

    assert rows == [{"updated_at": "2087-05-11T10:00:00Z"}]


def test_memory_verify_detects_materialized_card_mismatch(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        conn.execute("update memory_cards set claim = ?", ("篡改后的 materialized claim",))
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 1
    assert result.invalid_events == 0
    assert result.materialization_mismatches == 1


def test_memory_verify_detects_tampered_payload(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        event = fetch_all(conn, "select event_id, payload_json from signed_events")[0]
        payload = json.loads(event["payload_json"])
        payload["claim"] = "篡改后的 claim"
        conn.execute(
            "update signed_events set payload_json = ? where event_id = ?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), event["event_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 0
    assert result.invalid_events == 1


def test_memory_verify_detects_broken_owner_hash_chain(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path, candidate_id="cand_test_001", claim="用户要求音频本地处理。")
    _insert_candidate(config.database_path, candidate_id="cand_test_002", claim="用户决定保留本地证据链。")
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        second = fetch_all(
            conn,
            "select event_hash from signed_events where owner_sequence = 2",
        )[0]
        conn.execute(
            "update signed_events set prev_event_hash = ? where event_hash = ?",
            ("sha256:broken", second["event_hash"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 1
    assert result.invalid_events == 1


def test_memory_verify_preserves_verified_unsupported_events(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        card = MemoryCard(
            card_id="mem_test_001",
            owner_did="did:key:test-owner",
            claim_type="decision",
            claim="Use signed events.",
            subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
            evidence_refs=[
                EvidenceRef(
                    evidence_id="ev_test",
                    source_type="transcript_segment",
                    source_id="seg_test",
                    quote="Use signed events.",
                )
            ],
        )
        event, public_key = create_signed_event(
            event_type="future_protocol.created",
            payload=card,
            signer_did="did:key:test-owner",
        )
        insert_signed_event(conn, event=event, public_key=public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 1
    assert result.invalid_events == 0
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select trust_status from signed_events")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert rows == [{"trust_status": "unsupported"}]
    assert cards == []


def test_memory_verify_rejects_owner_sequence_forks(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        first = _memory_card("mem_test_001", "Use signed events.")
        second = _memory_card("mem_test_002", "Use hash chains.")
        first_event, first_public_key = create_signed_event(
            event_type="memory_card.created",
            payload=first,
            signer_did="did:key:test-owner",
            owner_sequence=1,
        )
        second_event, second_public_key = create_signed_event(
            event_type="memory_card.created",
            payload=second,
            signer_did="did:key:test-owner",
            owner_sequence=1,
        )
        _insert_unverified_event(conn, event=first_event, public_key=first_public_key)
        _insert_unverified_event(conn, event=second_event, public_key=second_public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 0
    assert result.invalid_events == 2
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select trust_status from signed_events order by event_hash")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert rows == [{"trust_status": "rejected"}, {"trust_status": "rejected"}]
    assert cards == []


def test_memory_verify_rejects_object_version_conflicts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        first = _memory_card("mem_test_001", "Use signed events.")
        second = _memory_card("mem_test_001", "Use a different claim for the same object version.")
        first_event, first_public_key = create_signed_event(
            event_type="memory_card.created",
            payload=first,
            signer_did="did:key:test-owner",
            owner_sequence=1,
            object_version=1,
        )
        second_event, second_public_key = create_signed_event(
            event_type="memory_card.created",
            payload=second,
            signer_did="did:key:test-owner",
            owner_sequence=2,
            prev_event_hash=first_event.event_hash,
            object_version=1,
        )
        _insert_unverified_event(conn, event=first_event, public_key=first_public_key)
        _insert_unverified_event(conn, event=second_event, public_key=second_public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 0
    assert result.invalid_events == 2
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select trust_status from signed_events order by event_hash")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert rows == [{"trust_status": "rejected"}, {"trust_status": "rejected"}]
    assert cards == []


def _insert_candidate(
    database_path: Path,
    *,
    candidate_id: str = "cand_test_001",
    claim: str = "用户要求音频本地处理。",
) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                claim,
                "requirement",
                json.dumps({"type": "project", "id": "personal_context_node", "label": "Personal Context Node"}),
                0.95,
                json.dumps(
                    [
                        {
                            "evidence_id": "ev_test",
                            "source_type": "transcript_segment",
                            "source_id": "seg_test",
                            "quote": "音频必须本地处理。",
                        }
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "pending_review",
                None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_review_stable(path: Path) -> None:
    stable_time = time.time() - 121
    os.utime(path, (stable_time, stable_time))


def _memory_card(card_id: str, claim: str) -> MemoryCard:
    return MemoryCard(
        card_id=card_id,
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim=claim,
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id=f"ev_{card_id}",
                source_type="transcript_segment",
                source_id=f"seg_{card_id}",
                quote=claim,
            )
        ],
    )


def _insert_unverified_event(conn, *, event, public_key: str) -> None:
    signing_body_json = json.dumps(signing_body(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw_event_json = event.model_dump_json()
    conn.execute(
        """
        insert into signed_events (
          event_hash, event_id, event_type, signer_did,
          owner_id, owner_sequence, prev_event_hash, envelope_version,
          object_id, object_version, payload_type, payload_encoding,
          created_at, payload_json, raw_event_json, signing_body_json,
          canonical_signing_body_hash, signature_algorithm, public_key_id,
          signature_value, trust_status, event_json, signature, public_key, verified
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_hash,
            event.event_hash,
            event.event_type,
            event.signer_did,
            event.owner_id,
            event.owner_sequence,
            event.prev_event_hash,
            event.envelope_version,
            event.object_id,
            event.object_version,
            event.payload_type,
            event.payload_encoding,
            event.created_at,
            json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
            raw_event_json,
            signing_body_json,
            event.event_hash,
            event.signature.algorithm,
            event.signature.public_key_id,
            event.signature.value,
            "unverified",
            raw_event_json,
            event.signature.value,
            public_key,
            0,
        ),
    )
