from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryAnnotation,
    MemoryAnnotationRevocation,
    MemoryCard,
    MemoryCardMetadataUpdate,
    MemoryCardRevocation,
    MemoryCardSupersession,
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


def test_memory_card_revocation_actor_must_match_signer(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    owner_key = Ed25519PrivateKey.generate()
    card = _memory_card(owner_did="did:key:card-owner")
    card_event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
        private_key=owner_key,
    )
    rejected_event, _ = create_signed_event(
        event_type="memory_card.revoked",
        payload=MemoryCardRevocation(card_id=card.card_id, revoked_by="did:key:someone-else"),
        signer_did=card.owner_did,
        private_key=owner_key,
        owner_sequence=2,
        prev_event_hash=card_event.event_hash,
        object_version=2,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=card_event, public_key=public_key)
        insert_signed_event(conn, event=rejected_event, public_key=public_key)
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


def test_memory_card_metadata_update_actor_must_match_signer(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    owner_key = Ed25519PrivateKey.generate()
    card = _memory_card(owner_did="did:key:card-owner")
    card_event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
        private_key=owner_key,
    )
    rejected_event, _ = create_signed_event(
        event_type="memory_card.metadata_updated",
        payload=MemoryCardMetadataUpdate(card_id=card.card_id, updated_by="did:key:someone-else", tags=["bad-actor"]),
        signer_did=card.owner_did,
        private_key=owner_key,
        owner_sequence=2,
        prev_event_hash=card_event.event_hash,
        object_version=2,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=card_event, public_key=public_key)
        insert_signed_event(conn, event=rejected_event, public_key=public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 1
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        cards = fetch_all(conn, "select card_id, tags_json, source_event_hash from memory_cards")
    finally:
        conn.close()
    assert cards == [{"card_id": card.card_id, "tags_json": "[]", "source_event_hash": card_event.event_hash}]


def test_memory_card_supersession_actor_must_match_signer(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    owner_key = Ed25519PrivateKey.generate()
    card = _memory_card(owner_did="did:key:card-owner")
    replacement = _memory_card(owner_did="did:key:card-owner", card_id="mem_replacement_authority_test")
    card_event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
        private_key=owner_key,
        owner_sequence=1,
    )
    replacement_event, _ = create_signed_event(
        event_type="memory_card.created",
        payload=replacement,
        signer_did=replacement.owner_did,
        private_key=owner_key,
        owner_sequence=2,
        prev_event_hash=card_event.event_hash,
    )
    rejected_event, _ = create_signed_event(
        event_type="memory_card.superseded",
        payload=MemoryCardSupersession(
            card_id=card.card_id,
            superseded_by_card_id=replacement.card_id,
            superseded_by="did:key:someone-else",
        ),
        signer_did=card.owner_did,
        private_key=owner_key,
        owner_sequence=3,
        prev_event_hash=replacement_event.event_hash,
        object_version=2,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=card_event, public_key=public_key)
        insert_signed_event(conn, event=replacement_event, public_key=public_key)
        insert_signed_event(conn, event=rejected_event, public_key=public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 3
    assert result.valid_events == 2
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        cards = fetch_all(conn, "select card_id, status from memory_cards order by card_id")
    finally:
        conn.close()
    assert cards == [
        {"card_id": card.card_id, "status": "active"},
        {"card_id": replacement.card_id, "status": "active"},
    ]


def test_memory_annotation_revocation_actor_must_match_signer(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    owner_key = Ed25519PrivateKey.generate()
    author_key = Ed25519PrivateKey.generate()
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
        body="Only the annotation author can revoke this annotation.",
    )
    annotation_event, annotation_public_key = create_signed_event(
        event_type="memory_annotation.created",
        payload=annotation,
        signer_did=annotation.author,
        private_key=author_key,
    )
    rejected_event, _ = create_signed_event(
        event_type="memory_annotation.revoked",
        payload=MemoryAnnotationRevocation(annotation_id=annotation.annotation_id, revoked_by="did:key:someone-else"),
        signer_did=annotation.author,
        private_key=author_key,
        owner_sequence=2,
        prev_event_hash=annotation_event.event_hash,
        object_version=2,
    )

    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=card_event, public_key=card_public_key)
        insert_signed_event(conn, event=annotation_event, public_key=annotation_public_key)
        insert_signed_event(conn, event=rejected_event, public_key=annotation_public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 3
    assert result.valid_events == 2
    assert result.invalid_events == 1
    conn = connect(config.database_path)
    try:
        annotations = fetch_all(conn, "select annotation_id, status, source_event_hash from memory_annotations")
    finally:
        conn.close()
    assert annotations == [
        {
            "annotation_id": annotation.annotation_id,
            "status": "active",
            "source_event_hash": annotation_event.event_hash,
        }
    ]


def _memory_card(*, owner_did: str, card_id: str = "mem_authority_test") -> MemoryCard:
    return MemoryCard(
        card_id=card_id,
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
