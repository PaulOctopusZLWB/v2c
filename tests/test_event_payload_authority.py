from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryAnnotation,
    MemoryCard,
    SubjectRef,
    create_signed_event,
)
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_card_created_by_non_owner_is_rejected(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    attacker_key = Ed25519PrivateKey.generate()
    card = _memory_card(owner_did="did:key:card-owner")
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did="did:key:attacker",
        private_key=attacker_key,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=event, public_key=public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 0
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        events = fetch_all(conn, "select event_type, owner_id, trust_status from signed_events")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert events == [
        {
            "event_type": "memory_card.created",
            "owner_id": "did:key:attacker",
            "trust_status": "rejected",
        }
    ]
    assert cards == []


def test_memory_annotation_created_by_non_author_is_rejected(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    owner_key = Ed25519PrivateKey.generate()
    attacker_key = Ed25519PrivateKey.generate()
    card = _memory_card(owner_did="did:key:card-owner")
    card_event, card_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
        private_key=owner_key,
    )
    annotation = MemoryAnnotation(
        annotation_id="ann_authority_test",
        target_card_id=card.card_id,
        author="did:key:commenter",
        annotation_type="comment",
        body="Only the annotation author can publish this annotation.",
    )
    annotation_event, attacker_public_key = create_signed_event(
        event_type="memory_annotation.created",
        payload=annotation,
        signer_did="did:key:attacker",
        private_key=attacker_key,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=card_event, public_key=card_public_key)
        insert_signed_event(conn, event=annotation_event, public_key=attacker_public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 1
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        annotations = fetch_all(conn, "select annotation_id from memory_annotations")
    finally:
        conn.close()
    assert annotations == []


def _memory_card(*, owner_did: str) -> MemoryCard:
    return MemoryCard(
        card_id="mem_authority_test",
        owner_did=owner_did,
        claim_type="decision",
        claim="Created events must be signed by the payload owner.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_authority_test",
                source_type="transcript_segment",
                source_id="seg_authority_test",
                quote="Created events must be signed by the payload owner.",
            )
        ],
    )
