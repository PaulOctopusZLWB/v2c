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
