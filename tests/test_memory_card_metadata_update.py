from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryCard,
    MemoryCardMetadataUpdate,
    SubjectRef,
    create_signed_event,
)
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_card_metadata_updated_event_updates_metadata_without_changing_claim(tmp_path: Path) -> None:
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
        update = MemoryCardMetadataUpdate(
            card_id=card.card_id,
            updated_by=card.owner_did,
            visibility="public",
            tags=["asr", "local-first"],
        )
        updated_event, _ = create_signed_event(
            event_type="memory_card.metadata_updated",
            payload=update,
            signer_did=card.owner_did,
            private_key=private_key,
            owner_sequence=2,
            prev_event_hash=created_event.event_hash,
            object_version=2,
        )

        insert_signed_event(conn, event=updated_event, public_key=public_key)

        rows = fetch_all(
            conn,
            """
            select card_id, claim, visibility_json, tags_json, status, source_event_hash
            from memory_cards
            """,
        )
    finally:
        conn.close()

    assert rows == [
        {
            "card_id": "mem_test_001",
            "claim": "Use signed events.",
            "visibility_json": json.dumps({"type": "public"}, ensure_ascii=False, sort_keys=True),
            "tags_json": json.dumps(["asr", "local-first"], ensure_ascii=False, sort_keys=True),
            "status": "active",
            "source_event_hash": updated_event.event_hash,
        }
    ]


def test_memory_card_metadata_update_materializes_when_update_arrives_before_card(tmp_path: Path) -> None:
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
        update = MemoryCardMetadataUpdate(
            card_id=card.card_id,
            updated_by=card.owner_did,
            visibility={"type": "group", "group_id": "grp_test"},
            tags=["protocol"],
        )
        updated_event, _ = create_signed_event(
            event_type="memory_card.metadata_updated",
            payload=update,
            signer_did=card.owner_did,
            private_key=private_key,
            owner_sequence=2,
            prev_event_hash=created_event.event_hash,
            object_version=2,
        )

        insert_signed_event(conn, event=updated_event, public_key=public_key)
        insert_signed_event(conn, event=created_event, public_key=public_key)

        rows = fetch_all(conn, "select card_id, visibility_json, tags_json, source_event_hash from memory_cards")
    finally:
        conn.close()

    assert rows == [
        {
            "card_id": "mem_test_001",
            "visibility_json": json.dumps({"type": "group", "group_id": "grp_test"}, ensure_ascii=False, sort_keys=True),
            "tags_json": json.dumps(["protocol"], ensure_ascii=False, sort_keys=True),
            "source_event_hash": updated_event.event_hash,
        }
    ]


def test_memory_verify_accepts_metadata_updated_materialized_card(tmp_path: Path) -> None:
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
        updated_event, _ = create_signed_event(
            event_type="memory_card.metadata_updated",
            payload=MemoryCardMetadataUpdate(
                card_id=card.card_id,
                updated_by=card.owner_did,
                visibility={"type": "group", "group_id": "grp_test"},
                tags=["protocol"],
            ),
            signer_did=card.owner_did,
            private_key=private_key,
            owner_sequence=2,
            prev_event_hash=created_event.event_hash,
            object_version=2,
        )
        insert_signed_event(conn, event=updated_event, public_key=public_key)
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
