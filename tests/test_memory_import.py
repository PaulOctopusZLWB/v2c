from __future__ import annotations

import json
from pathlib import Path

import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import (
    EvidenceRef,
    EventSignature,
    MemoryCard,
    SubjectRef,
    canonical_json_bytes,
    create_signed_event,
)
from personal_context_node.memory_import import import_memory_events
from personal_context_node.storage.sqlite import connect, fetch_all


def test_import_memory_events_verifies_jsonl_and_materializes_cards(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    card = MemoryCard(
        card_id="mem_imported",
        owner_did="did:key:external-owner",
        claim_type="decision",
        claim="Imported memory events must be verified before materialization.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_imported",
                source_type="transcript_segment",
                source_id="seg_imported",
                quote="imported quote",
            )
        ],
    )
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )
    input_path = tmp_path / "events.jsonl"
    input_path.write_text(event.model_dump_json() + "\n", encoding="utf-8")

    result = import_memory_events(config=config, input_path=input_path, public_key=public_key)

    assert result.events_imported == 1
    assert result.trusted_events == 1
    assert result.rejected_events == 0
    conn = connect(config.database_path)
    try:
        events = fetch_all(conn, "select event_type, trust_status from signed_events")
        cards = fetch_all(conn, "select card_id, claim, status from memory_cards")
    finally:
        conn.close()
    assert events == [{"event_type": "memory_card.created", "trust_status": "trusted"}]
    assert cards == [
        {
            "card_id": "mem_imported",
            "claim": "Imported memory events must be verified before materialization.",
            "status": "active",
        }
    ]


def test_import_memory_events_ingests_spec_owner_field_and_quarantines_malformed(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    from personal_context_node.core.protocols.memory import SignedEvent, did_key_from_public_key

    # Two distinct owners (multi-owner JSONL) so the two seq-1 events are not a fork.
    spec_key = Ed25519PrivateKey.generate()
    spec_did = did_key_from_public_key(spec_key.public_key().public_bytes_raw())
    bad_key = Ed25519PrivateKey.generate()
    bad_did = did_key_from_public_key(bad_key.public_key().public_bytes_raw())

    def signed_line(key: Ed25519PrivateKey, did: str, payload: dict) -> str:
        body = {
            "envelope_version": "signed_event.v1",
            "event_type": "memory_card.created",
            "object_id": payload["card_id"],
            "object_version": 1,
            "owner_id": did,
            "owner_sequence": 1,
            "prev_event_hash": None,
            "payload_type": "memory_card.v1",
            "payload_encoding": "plain",
            "payload": payload,
            "created_at": "2026-06-10T00:00:00Z",
        }
        signature = key.sign(canonical_json_bytes(body))
        event = SignedEvent(
            **body,
            signature=EventSignature(
                public_key_id=did,
                value=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
            ),
        )
        return event.model_dump_json()

    # Spec-conformant card uses the protocol field name `owner` (§17.1); a malformed
    # v1 payload (missing required claim) must be quarantined, not crash the batch.
    spec_card = {
        "schema_version": "memory_card.v1",
        "card_id": "mem_owner_field",
        "owner": spec_did,
        "claim_type": "decision",
        "claim": "Spec cards use the owner field.",
        "subject": {"type": "project", "id": "pcn", "label": "PCN"},
        "evidence_refs": [],
        "source_type": "manual",
        "visibility": {"type": "private"},
    }
    malformed = {"schema_version": "memory_card.v1", "card_id": "mem_malformed", "owner": bad_did}
    input_path = tmp_path / "events.jsonl"
    input_path.write_text(
        signed_line(spec_key, spec_did, spec_card) + "\n" + signed_line(bad_key, bad_did, malformed) + "\n",
        encoding="utf-8",
    )

    result = import_memory_events(config=config, input_path=input_path)

    assert result.events_imported == 2
    assert result.trusted_events == 1
    assert result.unsupported_events == 1
    conn = connect(config.database_path)
    try:
        statuses = {
            row["object_id"]: row["trust_status"]
            for row in fetch_all(conn, "select object_id, trust_status from signed_events")
        }
        cards = [row["card_id"] for row in fetch_all(conn, "select card_id from memory_cards")]
    finally:
        conn.close()
    assert statuses == {"mem_owner_field": "trusted", "mem_malformed": "unsupported"}
    assert cards == ["mem_owner_field"]


def test_import_memory_events_rejects_invalid_signatures_without_materializing(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    card = MemoryCard(
        card_id="mem_rejected_import",
        owner_did="did:key:external-owner",
        claim_type="decision",
        claim="Tampered imports must not materialize.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_rejected_import",
                source_type="transcript_segment",
                source_id="seg_rejected_import",
                quote="tampered quote",
            )
        ],
    )
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )
    raw = json.loads(event.model_dump_json())
    raw["payload"]["claim"] = "This claim was modified after signing."
    input_path = tmp_path / "events.jsonl"
    input_path.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    result = import_memory_events(config=config, input_path=input_path, public_key=public_key)

    assert result.events_imported == 1
    assert result.trusted_events == 0
    assert result.rejected_events == 1
    conn = connect(config.database_path)
    try:
        events = fetch_all(conn, "select event_type, trust_status from signed_events")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert events == [{"event_type": "memory_card.created", "trust_status": "rejected"}]
    assert cards == []


def test_import_memory_events_stores_broken_hash_chain_as_dangling(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    card = MemoryCard(
        card_id="mem_dangling_import",
        owner_did="did:key:external-owner",
        claim_type="decision",
        claim="Broken hash-chain imports must not materialize.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_dangling_import",
                source_type="transcript_segment",
                source_id="seg_dangling_import",
                quote="dangling quote",
            )
        ],
    )
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
        owner_sequence=2,
        prev_event_hash="sha256:missing-predecessor",
    )
    input_path = tmp_path / "events.jsonl"
    input_path.write_text(event.model_dump_json() + "\n", encoding="utf-8")

    result = import_memory_events(config=config, input_path=input_path, public_key=public_key)

    assert result.events_imported == 1
    assert result.trusted_events == 0
    assert result.rejected_events == 0
    assert result.unsupported_events == 0
    conn = connect(config.database_path)
    try:
        events = fetch_all(conn, "select event_type, trust_status from signed_events")
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert events == [{"event_type": "memory_card.created", "trust_status": "dangling"}]
    assert cards == []


def test_import_memory_events_preserves_signed_unknown_top_level_fields(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    private_key = Ed25519PrivateKey.generate()
    card = MemoryCard(
        card_id="mem_future_field_import",
        owner_did="did:key:external-owner",
        claim_type="decision",
        claim="Future signed fields must survive import and export.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_future_field_import",
                source_type="transcript_segment",
                source_id="seg_future_field_import",
                quote="future field quote",
            )
        ],
    )
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
        private_key=private_key,
    )
    raw = event.model_dump(mode="json")
    raw["future_extension"] = {"retention_policy": "preserve"}
    body = {key: value for key, value in raw.items() if key != "signature"}
    signature = private_key.sign(canonical_json_bytes(body))
    raw["signature"] = EventSignature(
        public_key_id=card.owner_did,
        value=base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
    ).model_dump(mode="json")
    input_path = tmp_path / "events.jsonl"
    input_path.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    result = import_memory_events(config=config, input_path=input_path, public_key=public_key)

    assert result.events_imported == 1
    assert result.trusted_events == 1
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select raw_event_json from signed_events")
    finally:
        conn.close()
    stored_event = json.loads(rows[0]["raw_event_json"])
    assert stored_event["future_extension"] == {"retention_policy": "preserve"}


def test_memory_import_cli_imports_jsonl(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    input_path, public_key = _write_import_event(tmp_path, owner_did="did:key:cli-import")

    result = CliRunner().invoke(
        app,
        [
            "memory-import",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--input-path",
            str(input_path),
            "--public-key",
            public_key,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "events_imported=1" in result.output
    assert "trusted_events=1" in result.output


def test_memory_import_group_cli_imports_jsonl(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    input_path, public_key = _write_import_event(tmp_path, owner_did="did:key:group-import")

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "import",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--input-path",
            str(input_path),
            "--public-key",
            public_key,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "events_imported=1" in result.output
    assert "trusted_events=1" in result.output


def test_memory_import_group_cli_uses_config_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "configured-data"
    vault = tmp_path / "configured-vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(f"[paths]\ndata_dir = '{data_dir}'\nobsidian_vault = '{vault}'\n", encoding="utf-8")
    input_path, public_key = _write_import_event(tmp_path, owner_did="did:key:configured-import")

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "import",
            "--config",
            str(config_path),
            "--input-path",
            str(input_path),
            "--public-key",
            public_key,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "events_imported=1" in result.output
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    conn = connect(config.database_path)
    try:
        events = fetch_all(conn, "select event_type, trust_status from signed_events")
    finally:
        conn.close()
    assert events == [{"event_type": "memory_card.created", "trust_status": "trusted"}]


def _write_import_event(tmp_path: Path, *, owner_did: str) -> tuple[Path, str]:
    card = MemoryCard(
        card_id=f"mem_{owner_did.rsplit(':', 1)[-1].replace('-', '_')}",
        owner_did=owner_did,
        claim_type="decision",
        claim="CLI imported memory events are verified.",
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id="ev_cli_import",
                source_type="transcript_segment",
                source_id="seg_cli_import",
                quote="cli import quote",
            )
        ],
    )
    event, public_key = create_signed_event(
        event_type="memory_card.created",
        payload=card,
        signer_did=card.owner_did,
    )
    input_path = tmp_path / f"{card.card_id}.jsonl"
    input_path.write_text(event.model_dump_json() + "\n", encoding="utf-8")
    return input_path, public_key


def test_no_config_owner_did_round_trips_export_import(tmp_path: Path) -> None:
    # Even without `pcn init`/--config (placeholder owner_did default), events must be
    # minted under a real, signing-key-bound did:key so a clean export imports as
    # trusted on a fresh node (§30.3, §31.1). Otherwise the placeholder owner can't
    # self-certify and import rejects everything.
    from personal_context_node.core.protocols.memory import EvidenceRef, MemoryCard, SubjectRef
    from personal_context_node.identity_keys import effective_owner_did, load_or_create_signing_key
    from personal_context_node.memory_export import export_memory_events
    from personal_context_node.signed_event_store import create_chained_event, insert_signed_event
    from personal_context_node.storage.sqlite import connect, initialize

    source = AppConfig(data_dir=tmp_path / "src", obsidian_vault=tmp_path / "v1")
    assert source.owner_did == "did:key:local-owner"  # the placeholder default
    owner = effective_owner_did(source)
    assert owner.startswith("did:key:z")  # real, key-derived did:key

    conn = connect(source.database_path)
    try:
        initialize(conn)
        card = MemoryCard(
            card_id="mem_rt",
            owner_did=owner,
            claim_type="decision",
            claim="round trip",
            subject=SubjectRef(type="project", id="p", label="P"),
            evidence_refs=[EvidenceRef(evidence_id="ev", source_type="transcript_segment", source_id="s", quote="q")],
        )
        event, pk = create_chained_event(
            conn, event_type="memory_card.created", payload=card, signer_did=owner,
            private_key=load_or_create_signing_key(source),
        )
        insert_signed_event(conn, event=event, public_key=pk)
        conn.commit()
    finally:
        conn.close()
    export_path = tmp_path / "export.jsonl"
    export_memory_events(config=source, output_path=export_path, since="2000-01-01")

    fresh = AppConfig(data_dir=tmp_path / "dst", obsidian_vault=tmp_path / "v2")
    result = import_memory_events(config=fresh, input_path=export_path)  # no public_key

    assert result.trusted_events == 1
    assert result.rejected_events == 0
    conn = connect(fresh.database_path)
    try:
        cards = fetch_all(conn, "select card_id from memory_cards")
    finally:
        conn.close()
    assert cards == [{"card_id": "mem_rt"}]
