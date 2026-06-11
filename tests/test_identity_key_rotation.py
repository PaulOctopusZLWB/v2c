from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    IdentityKeyRotation,
    IdentityProfile,
    MemoryCard,
    MemoryCardRevocation,
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


def test_rotated_new_identity_rejects_non_profile_first_event(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
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
    first_new_card = MemoryCard(
        card_id="mem_new_chain_without_profile",
        owner_did=rotation.new_identity_id,
        claim_type="decision",
        claim="This new identity must publish predecessor profile first.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_new_chain_without_profile",
                source_type="transcript_segment",
                source_id="seg_new_chain_without_profile",
                quote="This new identity must publish predecessor profile first.",
            )
        ],
    )
    bad_new_event, new_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=first_new_card,
        signer_did=rotation.new_identity_id,
        private_key=new_key,
        owner_sequence=1,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=rotation_event, public_key=old_public_key)
        insert_signed_event(conn, event=bad_new_event, public_key=new_public_key)
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
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert events == [
        {
            "event_type": "memory_card.created",
            "owner_id": "did:key:new-owner",
            "trust_status": "rejected",
        },
        {
            "event_type": "identity_key.rotated",
            "owner_id": "did:key:old-owner",
            "trust_status": "trusted",
        },
    ]
    assert cards == []


def test_rotated_new_identity_accepts_predecessor_profile_as_first_event(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
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
    profile = IdentityProfile(
        identity_id=rotation.new_identity_id,
        display_name="Paul",
        public_key_multibase=rotation.new_public_key_multibase,
        predecessor={
            "identity_id": rotation.old_identity_id,
            "rotation_event_hash": rotation_event.event_hash,
        },
    )
    profile_event, new_public_key = create_signed_event(
        event_type="identity_profile.published",
        payload=profile,
        signer_did=profile.identity_id,
        private_key=new_key,
        owner_sequence=1,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=rotation_event, public_key=old_public_key)
        insert_signed_event(conn, event=profile_event, public_key=new_public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 2
    assert result.invalid_events == 0


def test_successor_identity_revocation_of_old_card_verifies_deterministically(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    old_key = Ed25519PrivateKey.generate()
    new_key = Ed25519PrivateKey.generate()
    old_card = MemoryCard(
        card_id="mem_old_identity_card",
        owner_did="did:key:old-owner",
        claim_type="decision",
        claim="This old identity card can be revoked by a valid successor identity.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_old_identity_card",
                source_type="transcript_segment",
                source_id="seg_old_identity_card",
                quote="This old identity card can be revoked by a valid successor identity.",
            )
        ],
    )
    old_card_event, old_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=old_card,
        signer_did=old_card.owner_did,
        private_key=old_key,
        owner_sequence=1,
    )
    rotation = IdentityKeyRotation(
        old_identity_id=old_card.owner_did,
        new_identity_id="did:key:new-owner",
        new_public_key_multibase="z6MnewOwner",
        reason="device_replaced",
    )
    rotation_event, _ = create_signed_event(
        event_type="identity_key.rotated",
        payload=rotation,
        signer_did=rotation.old_identity_id,
        private_key=old_key,
        owner_sequence=2,
        prev_event_hash=old_card_event.event_hash,
    )
    profile = IdentityProfile(
        identity_id=rotation.new_identity_id,
        display_name="Paul",
        public_key_multibase=rotation.new_public_key_multibase,
        predecessor={
            "identity_id": rotation.old_identity_id,
            "rotation_event_hash": rotation_event.event_hash,
        },
    )
    profile_event, new_public_key = create_signed_event(
        event_type="identity_profile.published",
        payload=profile,
        signer_did=profile.identity_id,
        private_key=new_key,
        owner_sequence=1,
    )
    revocation_event, _ = create_signed_event(
        event_type="memory_card.revoked",
        payload=MemoryCardRevocation(card_id=old_card.card_id, revoked_by=rotation.new_identity_id),
        signer_did=rotation.new_identity_id,
        private_key=new_key,
        owner_sequence=2,
        prev_event_hash=profile_event.event_hash,
        object_version=2,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=old_card_event, public_key=old_public_key)
        insert_signed_event(conn, event=rotation_event, public_key=old_public_key)
        insert_signed_event(conn, event=profile_event, public_key=new_public_key)
        insert_signed_event(conn, event=revocation_event, public_key=new_public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 4
    assert result.valid_events == 4
    assert result.invalid_events == 0
    assert result.materialization_mismatches == 0
    conn = connect(config.database_path)
    try:
        cards = fetch_all(conn, "select card_id, status, source_event_hash from memory_cards")
    finally:
        conn.close()
    assert cards == [
        {
            "card_id": old_card.card_id,
            "status": "revoked",
            "source_event_hash": revocation_event.event_hash,
        }
    ]
