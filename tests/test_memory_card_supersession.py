from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryCard,
    MemoryCardSupersession,
    SubjectRef,
    create_signed_event,
)
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_card_superseded_event_marks_old_card_superseded(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        private_key = Ed25519PrivateKey.generate()
        old_card = _memory_card("mem_old_001", "Use signed events.")
        new_card = _memory_card("mem_new_001", "Use signed event hash chains.")
        old_event, public_key = create_signed_event(
            event_type="memory_card.created",
            payload=old_card,
            signer_did=old_card.owner_did,
            private_key=private_key,
            owner_sequence=1,
        )
        insert_signed_event(conn, event=old_event, public_key=public_key)
        new_event, _ = create_signed_event(
            event_type="memory_card.created",
            payload=new_card,
            signer_did=new_card.owner_did,
            private_key=private_key,
            owner_sequence=2,
            prev_event_hash=old_event.event_hash,
        )
        insert_signed_event(conn, event=new_event, public_key=public_key)
        supersession = MemoryCardSupersession(
            card_id=old_card.card_id,
            superseded_by_card_id=new_card.card_id,
            superseded_by=old_card.owner_did,
            reason="semantic_update",
        )
        superseded_event, _ = create_signed_event(
            event_type="memory_card.superseded",
            payload=supersession,
            signer_did=old_card.owner_did,
            private_key=private_key,
            owner_sequence=3,
            prev_event_hash=new_event.event_hash,
            object_version=2,
        )

        insert_signed_event(conn, event=superseded_event, public_key=public_key)

        rows = fetch_all(conn, "select card_id, status, source_event_hash from memory_cards order by card_id")
    finally:
        conn.close()
    assert rows == [
        {
            "card_id": "mem_new_001",
            "status": "active",
            "source_event_hash": new_event.event_hash,
        },
        {
            "card_id": "mem_old_001",
            "status": "superseded",
            "source_event_hash": superseded_event.event_hash,
        },
    ]


def test_memory_card_supersession_materializes_when_successor_arrives_before_card(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        private_key = Ed25519PrivateKey.generate()
        old_card = _memory_card("mem_old_001", "Use signed events.")
        new_card = _memory_card("mem_new_001", "Use signed event hash chains.")
        old_event, public_key = create_signed_event(
            event_type="memory_card.created",
            payload=old_card,
            signer_did=old_card.owner_did,
            private_key=private_key,
            owner_sequence=1,
        )
        new_event, _ = create_signed_event(
            event_type="memory_card.created",
            payload=new_card,
            signer_did=new_card.owner_did,
            private_key=private_key,
            owner_sequence=2,
            prev_event_hash=old_event.event_hash,
        )
        superseded_event, _ = create_signed_event(
            event_type="memory_card.superseded",
            payload=MemoryCardSupersession(
                card_id=old_card.card_id,
                superseded_by_card_id=new_card.card_id,
                superseded_by=old_card.owner_did,
            ),
            signer_did=old_card.owner_did,
            private_key=private_key,
            owner_sequence=3,
            prev_event_hash=new_event.event_hash,
            object_version=2,
        )

        insert_signed_event(conn, event=superseded_event, public_key=public_key)
        insert_signed_event(conn, event=old_event, public_key=public_key)
        insert_signed_event(conn, event=new_event, public_key=public_key)

        rows = fetch_all(conn, "select card_id, status, source_event_hash from memory_cards order by card_id")
    finally:
        conn.close()

    assert rows == [
        {
            "card_id": "mem_new_001",
            "status": "active",
            "source_event_hash": new_event.event_hash,
        },
        {
            "card_id": "mem_old_001",
            "status": "superseded",
            "source_event_hash": superseded_event.event_hash,
        },
    ]


def test_memory_verify_accepts_superseded_materialized_card(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        private_key = Ed25519PrivateKey.generate()
        old_card = _memory_card("mem_old_001", "Use signed events.")
        new_card = _memory_card("mem_new_001", "Use signed event hash chains.")
        old_event, public_key = create_signed_event(
            event_type="memory_card.created",
            payload=old_card,
            signer_did=old_card.owner_did,
            private_key=private_key,
            owner_sequence=1,
        )
        insert_signed_event(conn, event=old_event, public_key=public_key)
        new_event, _ = create_signed_event(
            event_type="memory_card.created",
            payload=new_card,
            signer_did=new_card.owner_did,
            private_key=private_key,
            owner_sequence=2,
            prev_event_hash=old_event.event_hash,
        )
        insert_signed_event(conn, event=new_event, public_key=public_key)
        superseded_event, _ = create_signed_event(
            event_type="memory_card.superseded",
            payload=MemoryCardSupersession(
                card_id=old_card.card_id,
                superseded_by_card_id=new_card.card_id,
                superseded_by=old_card.owner_did,
            ),
            signer_did=old_card.owner_did,
            private_key=private_key,
            owner_sequence=3,
            prev_event_hash=new_event.event_hash,
            object_version=2,
        )
        insert_signed_event(conn, event=superseded_event, public_key=public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 3
    assert result.valid_events == 3
    assert result.invalid_events == 0
    assert result.materialization_mismatches == 0


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
