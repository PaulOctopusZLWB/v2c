from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EventSignature,
    EvidenceRef,
    MemoryCard,
    MemoryCardRevocation,
    SignedEvent,
    SubjectRef,
    canonical_json_bytes,
    create_signed_event,
    signing_body,
)
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_card_created_object_id_must_match_payload_card_id(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    private_key = Ed25519PrivateKey.generate()
    card = _memory_card()
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
        private_key=private_key,
    )
    mismatched_event = _resign_with_object_id(event, private_key=private_key, object_id="mem_wrong_object")

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=mismatched_event, public_key=public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 0
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert cards == []


def test_memory_card_revocation_object_id_must_match_payload_card_id(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    private_key = Ed25519PrivateKey.generate()
    card = _memory_card()
    card_event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
        private_key=private_key,
        owner_sequence=1,
    )
    revocation_event, _ = create_signed_event(
        event_type="memory_card.revoked",
        payload=MemoryCardRevocation(card_id=card.card_id, revoked_by=card.owner_did),
        signer_did=card.owner_did,
        private_key=private_key,
        owner_sequence=2,
        prev_event_hash=card_event.event_hash,
        object_version=2,
    )
    mismatched_revocation = _resign_with_object_id(
        revocation_event,
        private_key=private_key,
        object_id="mem_wrong_object",
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=card_event, public_key=public_key)
        insert_signed_event(conn, event=mismatched_revocation, public_key=public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 1
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        cards = fetch_all(conn, "select card_id, status, source_event_hash from memory_cards")
    finally:
        conn.close()
    assert cards == [{"card_id": card.card_id, "status": "active", "source_event_hash": card_event.event_hash}]


def _resign_with_object_id(event: SignedEvent, *, private_key: Ed25519PrivateKey, object_id: str) -> SignedEvent:
    body = signing_body(event)
    body["object_id"] = object_id
    signature = private_key.sign(canonical_json_bytes(body))
    return SignedEvent(
        **body,
        signature=EventSignature(
            public_key_id=event.owner_id,
            value=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        ),
    )


def _memory_card() -> MemoryCard:
    return MemoryCard(
        card_id="mem_object_identity_test",
        owner_did="did:key:card-owner",
        claim_type="decision",
        claim="Envelope object_id must match the payload object identity.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_object_identity_test",
                source_type="transcript_segment",
                source_id="seg_object_identity_test",
                quote="Envelope object_id must match the payload object identity.",
            )
        ],
    )
