from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    IdentityKeyRotation,
    MemoryCard,
    SubjectRef,
    create_signed_event,
)
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_verify_rejects_old_identity_events_after_key_rotation(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    old_key = Ed25519PrivateKey.generate()
    rotation = IdentityKeyRotation(
        old_identity_id="did:key:old-owner",
        new_identity_id="did:key:new-owner",
        new_public_key_multibase="z6MnewOwner",
        reason="device_replaced",
    )
    rotation_event, old_public_key = create_signed_event(
        event_type="identity_key.rotated",
        payload=rotation,
        signer_did=rotation.old_identity_id,
        private_key=old_key,
        owner_sequence=1,
    )
    late_card = MemoryCard(
        card_id="mem_after_rotation",
        owner_did=rotation.old_identity_id,
        claim_type="decision",
        claim="This old identity event must be rejected.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_after_rotation",
                source_type="transcript_segment",
                source_id="seg_after_rotation",
                quote="This old identity event must be rejected.",
            )
        ],
    )
    late_event, _ = create_signed_event(
        event_type="memory_card.created",
        payload=late_card,
        signer_did=rotation.old_identity_id,
        private_key=old_key,
        owner_sequence=2,
        prev_event_hash=rotation_event.event_hash,
    )
    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=rotation_event, public_key=old_public_key)
        insert_signed_event(conn, event=late_event, public_key=old_public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 1
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        events = fetch_all(conn, "select event_type, trust_status from signed_events order by owner_sequence")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert events == [
        {"event_type": "identity_key.rotated", "trust_status": "trusted"},
        {"event_type": "memory_card.created", "trust_status": "rejected"},
    ]
    assert cards == []
