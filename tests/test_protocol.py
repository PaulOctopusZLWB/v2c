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


def test_memory_card_rejects_local_person_subject_id() -> None:
    try:
        MemoryCard(
            card_id="mem_local_person_subject",
            owner_did="did:key:test-owner",
            claim_type="fact",
            claim="Person A prefers async status updates.",
            subject=SubjectRef(type="person", id="per_local_guest", label="Person A"),
            evidence_refs=[
                EvidenceRef(
                    evidence_id="ev_local_person_subject",
                    source_type="transcript_segment",
                    source_id="seg_local_person_subject",
                    quote="Person A prefers async status updates.",
                )
            ],
        )
    except ValueError as exc:
        assert "local person id" in str(exc)
    else:
        raise AssertionError("MemoryCard accepted a local per_* person id in shared subject")


def test_manual_memory_card_allows_empty_evidence() -> None:
    card = MemoryCard(
        card_id="mem_manual_without_evidence",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Manual protocol test cards can omit evidence.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[],
        source_type="manual",
    )

    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )

    assert card.source_type == "manual"
    assert event.payload["source_type"] == "manual"
    assert event.payload["evidence_refs"] == []
    assert verify_signed_event(event, public_key)


def test_memory_card_confidence_is_signed_payload_field() -> None:
    card = MemoryCard(
        card_id="mem_confidence_test",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Generated memory cards carry confidence.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_confidence_test",
                source_type="transcript_segment",
                source_id="seg_confidence_test",
                quote="Generated memory cards carry confidence.",
            )
        ],
        confidence=0.91,
    )

    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )

    assert event.payload["confidence"] == 0.91
    assert verify_signed_event(event, public_key)


def test_memory_card_temporal_bounds_are_signed_payload_fields() -> None:
    card = MemoryCard(
        card_id="mem_temporal_test",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Generated memory cards carry temporal bounds.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_temporal_test",
                source_type="transcript_segment",
                source_id="seg_temporal_test",
                quote="Generated memory cards carry temporal bounds.",
            )
        ],
        observed_at="2087-05-10T09:30:00Z",
        valid_from="2087-05-10",
        valid_until="2087-06-10",
    )

    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )

    assert event.payload["observed_at"] == "2087-05-10T09:30:00Z"
    assert event.payload["valid_from"] == "2087-05-10"
    assert event.payload["valid_until"] == "2087-06-10"
    assert verify_signed_event(event, public_key)


def test_memory_card_updated_at_is_signed_payload_field() -> None:
    card = MemoryCard(
        card_id="mem_updated_at_test",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Generated memory cards carry updated_at.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_updated_at_test",
                source_type="transcript_segment",
                source_id="seg_updated_at_test",
                quote="Generated memory cards carry updated_at.",
            )
        ],
        updated_at="2087-05-11T10:00:00Z",
    )

    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )

    assert event.payload["updated_at"] == "2087-05-11T10:00:00Z"
    assert verify_signed_event(event, public_key)


def test_memory_card_evidence_refs_preserve_shareable_metadata() -> None:
    card = MemoryCard(
        card_id="mem_evidence_metadata_test",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Shared memory cards carry evidence metadata.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_evidence_metadata_test",
                source_type="transcript_segment",
                source_id="seg_evidence_metadata_test",
                quote="Shared memory cards carry evidence metadata.",
                visibility="private",
                summary="Derived from local transcript on 2087-05-10.",
            )
        ],
    )

    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )

    assert event.payload["evidence_refs"] == [
        {
            "evidence_id": "ev_evidence_metadata_test",
            "source_type": "transcript_segment",
            "source_id": "seg_evidence_metadata_test",
            "quote": "Shared memory cards carry evidence metadata.",
            "visibility": "private",
            "summary": "Derived from local transcript on 2087-05-10.",
        }
    ]
    assert verify_signed_event(event, public_key)


def test_memory_card_visibility_defaults_to_private_object() -> None:
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

    assert card.visibility.model_dump(exclude_none=True) == {"type": "private"}


def test_memory_card_visibility_scalar_is_normalized_in_signed_payload() -> None:
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
        visibility="public",
    )

    event, _ = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )

    assert event.payload["visibility"] == {"type": "public"}


def test_materialize_cards_skips_unknown_payload_type() -> None:
    private_key = Ed25519PrivateKey.generate()
    card = MemoryCard(
        card_id="mem_future_payload_type",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Future payload versions must not enter the v1 readable view.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_future_payload_type",
                source_type="transcript_segment",
                source_id="seg_future_payload_type",
                quote="future payload version",
            )
        ],
    )
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
        private_key=private_key,
    )
    event_body = event.model_dump(mode="json", exclude={"signature"})
    event_body["payload_type"] = "memory_card.v2"
    signature = private_key.sign(canonical_json_bytes(event_body))
    future_event = SignedEvent(
        **event_body,
        signature=EventSignature(
            public_key_id=card.owner_did,
            value=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        ),
    )

    materialized = materialize_cards([future_event], {card.owner_did: public_key})

    assert materialized == {}


def test_unknown_memory_card_visibility_fails_closed_to_private() -> None:
    scalar = MemoryCard(
        card_id="mem_test_scalar",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Use signed events.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_test_scalar",
                source_type="transcript_segment",
                source_id="seg_test_scalar",
                quote="Use signed events.",
            )
        ],
        visibility="friends",
    )
    object_value = MemoryCard(
        card_id="mem_test_object",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Use signed events.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_test_object",
                source_type="transcript_segment",
                source_id="seg_test_object",
                quote="Use signed events.",
            )
        ],
        visibility={"type": "federated"},
    )

    assert scalar.visibility.model_dump(exclude_none=True) == {"type": "private"}
    assert object_value.visibility.model_dump(exclude_none=True) == {"type": "private"}


def test_parameterized_visibility_requires_full_object() -> None:
    scalar_group = MemoryCard(
        card_id="mem_test_scalar_group",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Group visibility requires a group id.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_test_scalar_group",
                source_type="transcript_segment",
                source_id="seg_test_scalar_group",
                quote="Group visibility requires a group id.",
            )
        ],
        visibility="group",
    )
    missing_group_id = MemoryCard(
        card_id="mem_test_missing_group",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Group visibility requires a group id.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_test_missing_group",
                source_type="transcript_segment",
                source_id="seg_test_missing_group",
                quote="Group visibility requires a group id.",
            )
        ],
        visibility={"type": "group"},
    )
    complete_group = MemoryCard(
        card_id="mem_test_complete_group",
        owner_did="did:key:test-owner",
        claim_type="decision",
        claim="Group visibility with a group id is valid.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_test_complete_group",
                source_type="transcript_segment",
                source_id="seg_test_complete_group",
                quote="Group visibility with a group id is valid.",
            )
        ],
        visibility={"type": "group", "group_id": "grp_test"},
    )

    assert scalar_group.visibility.model_dump(exclude_none=True) == {"type": "private"}
    assert missing_group_id.visibility.model_dump(exclude_none=True) == {"type": "private"}
    assert complete_group.visibility.model_dump(exclude_none=True) == {"type": "group", "group_id": "grp_test"}


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


def test_verified_unknown_payload_version_is_stored_as_unsupported(tmp_path) -> None:
    private_key = Ed25519PrivateKey.generate()
    event_body = {
        "envelope_version": "signed_event.v1",
        "event_type": "memory_card.created",
        "object_id": "mem_v2_001",
        "object_version": 1,
        "owner_id": "did:key:test-owner",
        "owner_sequence": 1,
        "prev_event_hash": None,
        "payload_type": "memory_card.v2",
        "payload_encoding": "plain",
        "payload": {"card_id": "mem_v2_001", "schema_version": "memory_card.v2", "claim": "future"},
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

        rows = fetch_all(conn, "select event_type, payload_type, trust_status from signed_events")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert rows == [
        {
            "event_type": "memory_card.created",
            "payload_type": "memory_card.v2",
            "trust_status": "unsupported",
        }
    ]
    assert cards == []
