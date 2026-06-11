from __future__ import annotations

from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    MemoryCard,
    SubjectRef,
    create_signed_event,
    materialize_cards,
    verify_signed_event,
)


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
