from __future__ import annotations

from pathlib import Path

from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryAnnotation,
    MemoryCard,
    SubjectRef,
    create_signed_event,
    verify_signed_event,
)
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_annotation_event_uses_annotation_id_as_object_id_and_verifies() -> None:
    annotation = MemoryAnnotation(
        annotation_id="ann_test_001",
        target_card_id="mem_test_001",
        author="did:key:commenter",
        annotation_type="confirm",
        body="我确认这是当前 v1 的 ASR 选择。",
    )

    event, public_key = create_signed_event(
        event_type="memory_annotation.created",
        payload=annotation,
        signer_did=annotation.author,
    )

    assert event.object_id == "ann_test_001"
    assert event.payload_type == "memory_annotation.v1"
    assert verify_signed_event(event, public_key)


def test_annotation_for_unknown_card_materializes_as_dangling(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        annotation = MemoryAnnotation(
            annotation_id="ann_test_001",
            target_card_id="mem_missing",
            author="did:key:commenter",
            annotation_type="confirm",
            body="我确认。",
        )
        event, public_key = create_signed_event(
            event_type="memory_annotation.created",
            payload=annotation,
            signer_did=annotation.author,
        )

        insert_signed_event(conn, event=event, public_key=public_key)

        rows = fetch_all(
            conn,
            "select annotation_id, target_card_id, author_did, annotation_type, body, status from memory_annotations",
        )
    finally:
        conn.close()
    assert rows == [
        {
            "annotation_id": "ann_test_001",
            "target_card_id": "mem_missing",
            "author_did": "did:key:commenter",
            "annotation_type": "confirm",
            "body": "我确认。",
            "status": "dangling",
        }
    ]


def test_dangling_annotation_becomes_active_when_target_card_arrives(tmp_path: Path) -> None:
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)
        annotation = MemoryAnnotation(
            annotation_id="ann_test_001",
            target_card_id="mem_test_001",
            author="did:key:commenter",
            annotation_type="comment",
            body="补充说明。",
        )
        annotation_event, annotation_public_key = create_signed_event(
            event_type="memory_annotation.created",
            payload=annotation,
            signer_did=annotation.author,
        )
        insert_signed_event(conn, event=annotation_event, public_key=annotation_public_key)

        card = MemoryCard(
            card_id="mem_test_001",
            owner_did="did:key:test-owner",
            claim_type="decision",
            claim="v1 使用 signed event log。",
            subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
            evidence_refs=[
                EvidenceRef(
                    evidence_id="ev_test",
                    source_type="transcript_segment",
                    source_id="seg_test",
                    quote="使用 signed event log。",
                )
            ],
        )
        card_event, card_public_key = create_signed_event(
            event_type="memory_card.created",
            payload=card,
            signer_did=card.owner_did,
        )
        insert_signed_event(conn, event=card_event, public_key=card_public_key)

        rows = fetch_all(conn, "select annotation_id, status from memory_annotations")
    finally:
        conn.close()
    assert rows == [{"annotation_id": "ann_test_001", "status": "active"}]
