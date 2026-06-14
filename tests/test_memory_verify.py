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
            """
            select claim, claim_type, current_version, owner_id, source_type, confidence, status, source_event_hash
            from memory_cards
            """,
        )
    finally:
        conn.close()

    assert cards == [
        {
            "claim": "用户要求音频本地处理。",
            "claim_type": "requirement",
            "current_version": 1,
            "owner_id": "did:key:test-owner",
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


def test_memory_verify_detects_tampered_subject_and_evidence(tmp_path: Path) -> None:
    # §33/§43: the materialization diff must cover claim-bearing columns. Tampering
    # subject_json (reattributing the claim) or evidence_refs_json must be detected.
    for column, forged in [
        ("subject_json", '{"type":"person","id":"did:key:zEVIL","label":"X"}'),
        ("evidence_refs_json", '[{"evidence_id":"ev_forged"}]'),
        ("candidate_claim", "篡改后的 candidate_claim"),
    ]:
        config = AppConfig(data_dir=tmp_path / column, obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
        _insert_candidate(config.database_path)
        review_path = publish_candidate_review(config=config, day="2087-05-10")
        review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
        _mark_review_stable(review_path)
        confirm_checked_candidates(config=config, day="2087-05-10")

        conn = connect(config.database_path)
        try:
            conn.execute(f"update memory_cards set {column} = ?", (forged,))
            conn.commit()
        finally:
            conn.close()

        result = verify_memory_events(config=config)
        assert result.materialization_mismatches == 1, column


def test_object_version_column_tamper_cannot_launder_a_fork(tmp_path: Path) -> None:
    # A DB-level tamper of the derived object_version column (leaving raw_event_json
    # cryptographically valid) must not hide an object-version fork (§43.9): trust is
    # judged from the signed raw event, so both conflicting updates stay rejected.
    import sqlite3

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from personal_context_node.core.protocols.memory import (
        EvidenceRef,
        MemoryCard,
        MemoryCardMetadataUpdate,
        SubjectRef,
        create_signed_event,
        did_key_from_public_key,
    )
    from personal_context_node.signed_event_store import insert_signed_event

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        key = Ed25519PrivateKey.generate()
        did = did_key_from_public_key(key.public_key().public_bytes_raw())
        card = MemoryCard(
            card_id="mem_x",
            owner_did=did,
            claim_type="decision",
            claim="real",
            subject=SubjectRef(type="project", id="p", label="P"),
            evidence_refs=[EvidenceRef(evidence_id="e", source_type="transcript_segment", source_id="s", quote="q")],
        )
        created, _ = create_signed_event(event_type="memory_card.created", payload=card, signer_did=did, private_key=key, owner_sequence=1, object_version=1)
        good = MemoryCardMetadataUpdate(card_id="mem_x", updated_by=did, tags=["good"])
        good_event, _ = create_signed_event(event_type="memory_card.metadata_updated", payload=good, signer_did=did, private_key=key, owner_sequence=2, object_version=2)
        evil = MemoryCardMetadataUpdate(card_id="mem_x", updated_by=did, tags=["EVIL"])
        evil_event, _ = create_signed_event(event_type="memory_card.metadata_updated", payload=evil, signer_did=did, private_key=key, owner_sequence=3, object_version=2)
        insert_signed_event(conn, event=created, public_key=None)
        insert_signed_event(conn, event=good_event, public_key=None)
        insert_signed_event(conn, event=evil_event, public_key=None)
        conn.commit()
    finally:
        conn.close()
    # Tamper the EVIL update's object_version column 2 -> 3 to try to hide the fork.
    raw = sqlite3.connect(config.database_path)
    raw.execute("update signed_events set object_version = 3 where event_hash = ?", (evil_event.event_hash,))
    raw.commit()
    raw.close()

    verify_memory_events(config=config)
    conn = connect(config.database_path)
    try:
        tags = fetch_all(conn, "select tags_json from memory_cards where card_id = 'mem_x'")[0]["tags_json"]
    finally:
        conn.close()
    assert "EVIL" not in tags  # the forged update never reaches the materialized view


def test_cross_owner_card_id_collision_does_not_censor_or_overwrite(tmp_path: Path) -> None:
    # §11/§43.9: a validly-signed event from a DIFFERENT owner reusing a victim's card_id
    # must not be treated as an object-version fork (which previously rejected BOTH events,
    # censoring the victim's card), nor overwrite it, nor crash the insert/import transaction
    # with a partial-unique-index IntegrityError. Exactly one owner is bound to the card_id
    # deterministically; the cross-owner intruder is cleanly rejected.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from personal_context_node.core.protocols.memory import (
        EvidenceRef,
        MemoryCard,
        SubjectRef,
        create_signed_event,
        did_key_from_public_key,
    )
    from personal_context_node.signed_event_store import insert_signed_event

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    def _card_created(claim: str):
        key = Ed25519PrivateKey.generate()
        did = did_key_from_public_key(key.public_key().public_bytes_raw())
        card = MemoryCard(
            card_id="mem_shared",
            owner_did=did,
            claim_type="decision",
            claim=claim,
            subject=SubjectRef(type="project", id="p", label="P"),
            evidence_refs=[EvidenceRef(evidence_id="e", source_type="transcript_segment", source_id="s", quote="q")],
        )
        event, public_key = create_signed_event(
            event_type="memory_card.created", payload=card, signer_did=did, private_key=key,
            owner_sequence=1, object_version=1,
        )
        return {"did": did, "event": event, "pk": public_key, "claim": claim}

    a = _card_created("claim-a")
    b = _card_created("claim-b")
    # Deterministically reproduce the crash-prone ordering: insert the LARGER-DID card first
    # (it becomes trusted), then the SMALLER-DID card second — the recompute must promote the
    # smaller-DID card to trusted while the larger one is still trusted in the DB. The
    # smallest (owner_id, owner_sequence, event_hash) is the deterministic bound owner.
    first, second = sorted((a, b), key=lambda c: c["did"], reverse=True)
    bound = second  # smaller DID wins the deterministic tiebreak

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=first["event"], public_key=first["pk"])  # must not crash
        insert_signed_event(conn, event=second["event"], public_key=second["pk"])  # must not crash
        conn.commit()
        cards = fetch_all(conn, "select card_id, owner_did, claim from memory_cards where card_id = 'mem_shared'")
        statuses = sorted(
            str(row["trust_status"])
            for row in fetch_all(conn, "select trust_status from signed_events")
        )
    finally:
        conn.close()

    # Exactly one card survives (not zero — no mutual censorship), bound to the smaller DID.
    assert len(cards) == 1
    assert cards[0]["owner_did"] == bound["did"]
    assert cards[0]["claim"] == bound["claim"]  # the non-bound owner cannot overwrite content
    # One creation is trusted, the cross-owner intruder is rejected (never two trusted rows).
    assert statuses == ["rejected", "trusted"]


def test_memory_verify_detects_tampered_raw_event_json(tmp_path: Path) -> None:
    # raw_event_json is the complete-event source of truth (§31.1) and what export
    # ships; tampering it (even if payload_json is untouched) must be detected.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        event = fetch_all(conn, "select event_id, raw_event_json from signed_events")[0]
        raw = json.loads(event["raw_event_json"])
        raw["payload"]["claim"] = "篡改后的 raw claim"
        conn.execute(
            "update signed_events set raw_event_json = ? where event_id = ?",
            (json.dumps(raw, ensure_ascii=False), event["event_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.invalid_events == 1
    assert result.valid_events == 0


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


def test_insert_signed_event_rejects_owner_sequence_forks_without_materializing(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    first = _memory_card("mem_live_fork_001", "Use signed events.")
    second = _memory_card("mem_live_fork_002", "Use hash chains.")
    first_event, first_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=first,
        signer_did=first.owner_did,
        owner_sequence=1,
    )
    second_event, second_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=second,
        signer_did=second.owner_did,
        owner_sequence=1,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=first_event, public_key=first_public_key)
        insert_signed_event(conn, event=second_event, public_key=second_public_key)
        conn.commit()
        rows = fetch_all(conn, "select event_hash, trust_status from signed_events order by event_hash")
        cards = fetch_all(conn, "select card_id from memory_cards order by card_id")
    finally:
        conn.close()

    assert {row["event_hash"] for row in rows} == {first_event.event_hash, second_event.event_hash}
    assert {row["trust_status"] for row in rows} == {"rejected"}
    assert cards == []


def test_insert_signed_event_rejects_object_version_forks_without_materializing(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    first = _memory_card("mem_live_object_fork", "Use signed events.")
    second = _memory_card("mem_live_object_fork", "Use a conflicting claim for the same object version.")
    first_event, first_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=first,
        signer_did=first.owner_did,
        owner_sequence=1,
        object_version=1,
    )
    second_event, second_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=second,
        signer_did=second.owner_did,
        owner_sequence=2,
        prev_event_hash=first_event.event_hash,
        object_version=1,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=first_event, public_key=first_public_key)
        insert_signed_event(conn, event=second_event, public_key=second_public_key)
        conn.commit()
        rows = fetch_all(conn, "select event_hash, trust_status from signed_events order by event_hash")
        cards = fetch_all(conn, "select card_id from memory_cards order by card_id")
    finally:
        conn.close()

    assert {row["event_hash"] for row in rows} == {first_event.event_hash, second_event.event_hash}
    assert {row["trust_status"] for row in rows} == {"rejected"}
    assert cards == []


def test_promoting_dangling_event_rejects_object_version_fork_without_materializing(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    first = _memory_card("mem_promoted_object_fork", "Use signed events.")
    second = _memory_card("mem_promoted_object_fork", "Use a conflicting claim promoted from dangling.")
    first_event, first_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=first,
        signer_did=first.owner_did,
        owner_sequence=1,
        object_version=1,
    )
    second_event, second_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=second,
        signer_did=second.owner_did,
        owner_sequence=2,
        prev_event_hash=first_event.event_hash,
        object_version=1,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=second_event, public_key=second_public_key)
        insert_signed_event(conn, event=first_event, public_key=first_public_key)
        conn.commit()
        rows = fetch_all(conn, "select event_hash, trust_status from signed_events order by event_hash")
        cards = fetch_all(conn, "select card_id from memory_cards order by card_id")
    finally:
        conn.close()

    assert {row["event_hash"] for row in rows} == {first_event.event_hash, second_event.event_hash}
    assert {row["trust_status"] for row in rows} == {"rejected"}
    assert cards == []


def test_memory_verify_keeps_broken_hash_chain_dangling(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        card = _memory_card("mem_dangling_verify", "Verify must not trust broken chains.")
        event, public_key = create_signed_event(
            event_type="memory_card.created",
            payload=card,
            signer_did=card.owner_did,
            owner_sequence=2,
            prev_event_hash="sha256:missing-predecessor",
        )
        _insert_unverified_event(conn, event=event, public_key=public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 0
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        events = fetch_all(conn, "select trust_status, verified from signed_events")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert events == [{"trust_status": "dangling", "verified": 1}]
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
              confidence, evidence_refs_json, status, memory_card_id, date_key
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "2087-05-10",
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
