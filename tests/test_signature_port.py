from __future__ import annotations

from personal_context_node.adapters.signature.local_ed25519 import LocalEd25519SignatureAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.signature import UnsignedEvent
from personal_context_node.core.protocols.memory import EvidenceRef, MemoryCard, SubjectRef


def test_local_ed25519_signature_adapter_signs_and_verifies_events(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    adapter = LocalEd25519SignatureAdapter(config=config)
    card = MemoryCard(
        card_id="mem_sig_test",
        owner_did=config.owner_did,
        claim_type="requirement",
        claim="签名端口必须能验签。",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_sig_test",
                source_type="transcript_segment",
                source_id="seg_sig_test",
                quote="签名端口必须能验签。",
            )
        ],
    )

    identity = adapter.load_identity()
    event = adapter.sign_event(UnsignedEvent(event_type="memory_card.created", payload=card))
    verified = adapter.verify_event(event)
    tampered = event.model_copy(update={"payload": {**event.payload, "claim": "tampered"}})
    rejected = adapter.verify_event(tampered)

    assert identity.identity_id == config.owner_did
    assert identity.public_key
    assert event.owner_id == config.owner_did
    assert verified.verified is True
    assert rejected.verified is False
    assert rejected.reason == "invalid signature"
