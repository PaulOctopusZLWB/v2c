from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import personal_context_node.core.protocols.memory as protocol
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    EventSignature,
    MemoryCard,
    SignedEvent,
    SubjectRef,
    canonical_json_bytes,
    create_signed_event,
    materialize_cards,
    verify_signed_event,
)
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


TEST_PUBLIC_KEY = "QyAhYdjx1KHpnWctWVaBhVVnzaW9d_WRiW_XtSZCiFg"
TEST_DID = "did:key:z6MkiyHn3pefN7Awu3gtU5hgSzBtu1i71Dv3qyGZw2F3ZKpB"


def test_signed_memory_card_event_verifies_and_materializes() -> None:
    card = MemoryCard(
        card_id="mem_test_001",
        owner_did="did:key:test-owner",
        claim_type="requirement",
        claim="ASR and raw transcripts must stay local.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_test_001",
                source_type="transcript_segment",
                source_id="seg_test_001",
                quote="ASR 和原始转写必须本地运行。",
            )
        ],
        candidate_claim="ASR 和原始转写必须本地运行。",
    )

    event, public_key = create_signed_event(
        event_type="memory_card.confirmed.v1",
        payload=card,
        signer_did="did:key:test-owner",
    )

    assert verify_signed_event(event, public_key)
    materialized = materialize_cards([event], {"did:key:test-owner": public_key})
    assert materialized["mem_test_001"].claim == "ASR and raw transcripts must stay local."


def test_generated_memory_card_requires_evidence() -> None:
    try:
        MemoryCard(
            card_id="mem_without_evidence",
            owner_did="did:key:test-owner",
            claim_type="fact",
            claim="A generated claim without evidence is invalid.",
            subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
            evidence_refs=[],
            candidate_claim="A generated claim without evidence is invalid.",
        )
    except ValueError as exc:
        assert "evidence" in str(exc)
    else:
        raise AssertionError("MemoryCard accepted a generated claim without evidence")


def test_protocol_vector_hash_and_signature_verify() -> None:
    event = SignedEvent(
        envelope_version="signed_event.v1",
        event_type="memory_card.created",
        object_id="mem_01J00000000000000000000000",
        object_version=1,
        owner_id=TEST_DID,
        owner_sequence=1,
        prev_event_hash=None,
        payload_type="memory_card.v1",
        payload_encoding="plain",
        payload={
            "candidate_claim": None,
            "card_id": "mem_01J00000000000000000000000",
            "claim": "Use signed event log for memory cards.",
            "claim_type": "decision",
            "confidence": 1,
            "created_at": "2026-06-10T00:00:00Z",
            "evidence_refs": [],
            "observed_at": "2026-06-10T00:00:00Z",
            "owner": TEST_DID,
            "schema_version": "memory_card.v1",
            "source_type": "manual",
            "subject": {"id": "project_test", "label": "Protocol Test", "type": "project"},
            "tags": ["test"],
            "updated_at": "2026-06-10T00:00:00Z",
            "valid_from": "2026-06-10",
            "valid_until": None,
            "visibility": {"type": "private"},
        },
        created_at="2026-06-10T00:00:00Z",
        signature={
            "algorithm": "Ed25519",
            "public_key_id": TEST_DID,
            "value": "mnQdXTH9WIeeTjEVBLoQNBL3_EVt-uxcqKPx0-UzbYJv6toenF91_kFCVULMrMc9pAUlHPmmezmY8CkClZndBA",
        },
    )

    assert protocol.canonical_signing_body_hash(event) == "sha256:3ad717fd8c90f5c092c13becaf860133d829fd2f95a408c6f036e9aad4301f08"
    assert verify_signed_event(event, TEST_PUBLIC_KEY)


def test_protocol_vector_tampered_envelope_field_fails_signature() -> None:
    event = SignedEvent(
        envelope_version="signed_event.v1",
        event_type="memory_card.created",
        object_id="mem_01J00000000000000000000000",
        object_version=1,
        owner_id=TEST_DID,
        owner_sequence=1,
        prev_event_hash=None,
        payload_type="memory_card.v1",
        payload_encoding="plain",
        payload={
            "candidate_claim": None,
            "card_id": "mem_01J00000000000000000000000",
            "claim": "Use signed event log for memory cards.",
            "claim_type": "decision",
            "confidence": 1,
            "created_at": "2026-06-10T00:00:00Z",
            "evidence_refs": [],
            "observed_at": "2026-06-10T00:00:00Z",
            "owner": TEST_DID,
            "schema_version": "memory_card.v1",
            "source_type": "manual",
            "subject": {"id": "project_test", "label": "Protocol Test", "type": "project"},
            "tags": ["test"],
            "updated_at": "2026-06-10T00:00:00Z",
            "valid_from": "2026-06-10",
            "valid_until": None,
            "visibility": {"type": "private"},
        },
        created_at="2026-06-10T00:00:00Z",
        signature={
            "algorithm": "Ed25519",
            "public_key_id": TEST_DID,
            "value": "mnQdXTH9WIeeTjEVBLoQNBL3_EVt-uxcqKPx0-UzbYJv6toenF91_kFCVULMrMc9pAUlHPmmezmY8CkClZndBA",
        },
    )
    tampered = event.model_copy(update={"owner_sequence": 2})

    assert not verify_signed_event(tampered, TEST_PUBLIC_KEY)


def test_protocol_vector_second_event_links_to_previous_hash() -> None:
    event = SignedEvent(
        envelope_version="signed_event.v1",
        event_type="memory_card.created",
        object_id="mem_01J00000000000000000000001",
        object_version=1,
        owner_id=TEST_DID,
        owner_sequence=2,
        prev_event_hash="sha256:3ad717fd8c90f5c092c13becaf860133d829fd2f95a408c6f036e9aad4301f08",
        payload_type="memory_card.v1",
        payload_encoding="plain",
        payload={
            "candidate_claim": None,
            "card_id": "mem_01J00000000000000000000001",
            "claim": "v1 的本地 ASR 主 backend 采用 FunASR + SenseVoice。",
            "claim_type": "decision",
            "confidence": 0.91,
            "created_at": "2026-06-10T00:10:00Z",
            "evidence_refs": [],
            "observed_at": "2026-06-10T00:00:00Z",
            "owner": TEST_DID,
            "schema_version": "memory_card.v1",
            "source_type": "manual",
            "subject": {"id": "project_test", "label": "Protocol Test", "type": "project"},
            "tags": ["test"],
            "updated_at": "2026-06-10T00:10:00Z",
            "valid_from": "2026-06-10",
            "valid_until": None,
            "visibility": {"type": "private"},
        },
        created_at="2026-06-10T00:10:00Z",
        signature={
            "algorithm": "Ed25519",
            "public_key_id": TEST_DID,
            "value": "mdx8x87KdNJmmJMiLZCNqBoKKvOlMMHKpCpaCVka0AH1B-vmfTJn70zcRGN01-t6sGQxW6Azna8Y5Lwymz7BAQ",
        },
    )

    assert protocol.canonical_signing_body_hash(event) == "sha256:65cf60c07fb75da68f496ea4d036d0dcf4d56f88ea50d804acce11df77e4c59f"
    assert event.prev_event_hash == "sha256:3ad717fd8c90f5c092c13becaf860133d829fd2f95a408c6f036e9aad4301f08"
    assert verify_signed_event(event, TEST_PUBLIC_KEY)


def test_verified_unknown_event_is_stored_as_unsupported(tmp_path) -> None:
    card = MemoryCard(
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
    event, public_key = create_signed_event(
        event_type="future_protocol.created",
        payload=card,
        signer_did="did:key:test-owner",
    )
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        insert_signed_event(conn, event=event, public_key=public_key)

        rows = fetch_all(conn, "select event_type, trust_status from signed_events")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert rows == [{"event_type": "future_protocol.created", "trust_status": "unsupported"}]
    assert cards == []


def test_verified_encrypted_payload_event_is_stored_as_unsupported(tmp_path) -> None:
    private_key = Ed25519PrivateKey.generate()
    event_body = {
        "envelope_version": "signed_event.v1",
        "event_type": "memory_card.created",
        "object_id": "mem_encrypted_001",
        "object_version": 1,
        "owner_id": "did:key:test-owner",
        "owner_sequence": 1,
        "prev_event_hash": None,
        "payload_type": "memory_card.v1",
        "payload_encoding": "encrypted",
        "payload": {"ciphertext": "base64:test"},
        "created_at": "2087-05-10T00:00:00Z",
    }
    signature = private_key.sign(canonical_json_bytes(event_body))
    event = SignedEvent(
        **event_body,
        signature=EventSignature(
            public_key_id="did:key:test-owner",
            value=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        ),
    )
    public_key = base64.urlsafe_b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode("ascii")
    conn = connect(tmp_path / "data" / "db.sqlite")
    try:
        initialize(conn)

        insert_signed_event(conn, event=event, public_key=public_key)

        rows = fetch_all(conn, "select event_type, payload_encoding, trust_status from signed_events")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert rows == [
        {
            "event_type": "memory_card.created",
            "payload_encoding": "encrypted",
            "trust_status": "unsupported",
        }
    ]
    assert cards == []
