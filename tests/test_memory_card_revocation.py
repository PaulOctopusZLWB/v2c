from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryCard,
    MemoryCardRevocation,
    SubjectRef,
    create_signed_event,
)
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_card_revoked_event_marks_card_revoked(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        private_key = Ed25519PrivateKey.generate()
        card = _memory_card()
        created_event, public_key = create_signed_event(
            event_type="memory_card.created",
            payload=card,
            signer_did=card.owner_did,
            private_key=private_key,
        )
        insert_signed_event(conn, event=created_event, public_key=public_key)
        revocation = MemoryCardRevocation(
            card_id=card.card_id,
            revoked_by=card.owner_did,
            reason="incorrect",
        )
        revoked_event, _ = create_signed_event(
            event_type="memory_card.revoked",
            payload=revocation,
            signer_did=card.owner_did,
            private_key=private_key,
            owner_sequence=2,
            prev_event_hash=created_event.event_hash,
            object_version=2,
        )

        insert_signed_event(conn, event=revoked_event, public_key=public_key)

        rows = fetch_all(conn, "select card_id, status, source_event_hash from memory_cards")
    finally:
        conn.close()
    assert rows == [
        {
            "card_id": "mem_test_001",
            "status": "revoked",
            "source_event_hash": revoked_event.event_hash,
        }
    ]


def test_memory_card_revocation_by_unrelated_identity_is_rejected(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        owner_key = Ed25519PrivateKey.generate()
        attacker_key = Ed25519PrivateKey.generate()
        card = _memory_card()
        created_event, owner_public_key = create_signed_event(
            event_type="memory_card.created",
            payload=card,
            signer_did=card.owner_did,
            private_key=owner_key,
        )
        insert_signed_event(conn, event=created_event, public_key=owner_public_key)
        rejected_event, attacker_public_key = create_signed_event(
            event_type="memory_card.revoked",
            payload=MemoryCardRevocation(card_id=card.card_id, revoked_by="did:key:attacker"),
            signer_did="did:key:attacker",
            private_key=attacker_key,
            object_version=2,
        )

        insert_signed_event(conn, event=rejected_event, public_key=attacker_public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 1
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        events = fetch_all(conn, "select event_type, owner_id, trust_status from signed_events order by owner_id")
        cards = fetch_all(conn, "select card_id, status, source_event_hash from memory_cards")
    finally:
        conn.close()
    assert events == [
        {
            "event_type": "memory_card.revoked",
            "owner_id": "did:key:attacker",
            "trust_status": "rejected",
        },
        {
            "event_type": "memory_card.created",
            "owner_id": card.owner_did,
            "trust_status": "trusted",
        },
    ]
    assert cards == [
        {
            "card_id": card.card_id,
            "status": "active",
            "source_event_hash": created_event.event_hash,
        }
    ]


def test_memory_card_revocation_materializes_when_revocation_arrives_before_card(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        private_key = Ed25519PrivateKey.generate()
        card = _memory_card()
        created_event, public_key = create_signed_event(
            event_type="memory_card.created",
            payload=card,
            signer_did=card.owner_did,
            private_key=private_key,
            owner_sequence=1,
        )
        revoked_event, _ = create_signed_event(
            event_type="memory_card.revoked",
            payload=MemoryCardRevocation(card_id=card.card_id, revoked_by=card.owner_did),
            signer_did=card.owner_did,
            private_key=private_key,
            owner_sequence=2,
            prev_event_hash=created_event.event_hash,
            object_version=2,
        )

        insert_signed_event(conn, event=revoked_event, public_key=public_key)
        insert_signed_event(conn, event=created_event, public_key=public_key)

        rows = fetch_all(conn, "select card_id, status, source_event_hash from memory_cards")
    finally:
        conn.close()

    assert rows == [
        {
            "card_id": "mem_test_001",
            "status": "revoked",
            "source_event_hash": revoked_event.event_hash,
        }
    ]


def test_memory_verify_accepts_revoked_materialized_card(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        private_key = Ed25519PrivateKey.generate()
        card = _memory_card()
        created_event, public_key = create_signed_event(
            event_type="memory_card.created",
            payload=card,
            signer_did=card.owner_did,
            private_key=private_key,
        )
        insert_signed_event(conn, event=created_event, public_key=public_key)
        revoked_event, _ = create_signed_event(
            event_type="memory_card.revoked",
            payload=MemoryCardRevocation(card_id=card.card_id, revoked_by=card.owner_did),
            signer_did=card.owner_did,
            private_key=private_key,
            owner_sequence=2,
            prev_event_hash=created_event.event_hash,
            object_version=2,
        )
        insert_signed_event(conn, event=revoked_event, public_key=public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 2
    assert result.invalid_events == 0
    assert result.materialization_mismatches == 0


def _memory_card() -> MemoryCard:
    return MemoryCard(
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
