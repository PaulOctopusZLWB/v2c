from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryAnnotation,
    MemoryAnnotationRevocation,
    MemoryCard,
    SubjectRef,
    create_signed_event,
)
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_annotation_revoked_event_marks_annotation_revoked(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        owner_key = Ed25519PrivateKey.generate()
        author_key = Ed25519PrivateKey.generate()
        card = _memory_card()
        card_event, card_public_key = create_signed_event(
            event_type="memory_card.created",
            payload=card,
            signer_did=card.owner_did,
            private_key=owner_key,
        )
        insert_signed_event(conn, event=card_event, public_key=card_public_key)
        annotation = MemoryAnnotation(
            annotation_id="ann_test_001",
            target_card_id=card.card_id,
            author="did:key:commenter",
            annotation_type="comment",
            body="补充说明。",
        )
        annotation_event, annotation_public_key = create_signed_event(
            event_type="memory_annotation.created",
            payload=annotation,
            signer_did=annotation.author,
            private_key=author_key,
        )
        insert_signed_event(conn, event=annotation_event, public_key=annotation_public_key)
        revocation = MemoryAnnotationRevocation(
            annotation_id=annotation.annotation_id,
            revoked_by=annotation.author,
            reason="withdrawn",
        )
        revoked_event, _ = create_signed_event(
            event_type="memory_annotation.revoked",
            payload=revocation,
            signer_did=annotation.author,
            private_key=author_key,
            owner_sequence=2,
            prev_event_hash=annotation_event.event_hash,
            object_version=2,
        )

        insert_signed_event(conn, event=revoked_event, public_key=annotation_public_key)

        rows = fetch_all(conn, "select annotation_id, status, source_event_hash from memory_annotations")
    finally:
        conn.close()
    assert rows == [
        {
            "annotation_id": "ann_test_001",
            "status": "revoked",
            "source_event_hash": revoked_event.event_hash,
        }
    ]


def test_memory_verify_accepts_revoked_annotation(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        owner_key = Ed25519PrivateKey.generate()
        author_key = Ed25519PrivateKey.generate()
        card = _memory_card()
        card_event, card_public_key = create_signed_event(
            event_type="memory_card.created",
            payload=card,
            signer_did=card.owner_did,
            private_key=owner_key,
        )
        insert_signed_event(conn, event=card_event, public_key=card_public_key)
        annotation = MemoryAnnotation(
            annotation_id="ann_test_001",
            target_card_id=card.card_id,
            author="did:key:commenter",
            annotation_type="confirm",
            body="我确认。",
        )
        annotation_event, annotation_public_key = create_signed_event(
            event_type="memory_annotation.created",
            payload=annotation,
            signer_did=annotation.author,
            private_key=author_key,
        )
        insert_signed_event(conn, event=annotation_event, public_key=annotation_public_key)
        revoked_event, _ = create_signed_event(
            event_type="memory_annotation.revoked",
            payload=MemoryAnnotationRevocation(annotation_id=annotation.annotation_id, revoked_by=annotation.author),
            signer_did=annotation.author,
            private_key=author_key,
            owner_sequence=2,
            prev_event_hash=annotation_event.event_hash,
            object_version=2,
        )
        insert_signed_event(conn, event=revoked_event, public_key=annotation_public_key)
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 3
    assert result.valid_events == 3
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
