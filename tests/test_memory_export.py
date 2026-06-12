from __future__ import annotations

import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.core.protocols.memory import EvidenceRef, MemoryCard, SubjectRef, create_signed_event
from personal_context_node.memory_export import export_memory_events
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.signed_event_store import insert_signed_event
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_export_memory_events_writes_trusted_raw_events_jsonl(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")
    output_path = tmp_path / "events.jsonl"

    result = export_memory_events(config=config, output_path=output_path, since="2000-01-01")

    assert result.events_exported == 1
    exported = output_path.read_text(encoding="utf-8").splitlines()
    assert len(exported) == 1
    assert json.loads(exported[0])["event_type"] == "memory_card.created"
    conn = connect(config.database_path)
    try:
        raw_event = fetch_all(conn, "select raw_event_json from signed_events")[0]["raw_event_json"]
    finally:
        conn.close()
    assert exported[0] == raw_event


def test_export_memory_events_preserves_unsupported_raw_events_without_rejected(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    trusted_card = _memory_card("mem_export_trusted", "Trusted memory events are shareable.")
    trusted_event, trusted_public_key = create_signed_event(
        event_type="memory_card.created",
        payload=trusted_card,
        signer_did=trusted_card.owner_did,
    )
    future_card = _memory_card("mem_export_future", "Future events must be preserved.")
    unsupported_event, unsupported_public_key = create_signed_event(
        event_type="future_protocol.created",
        payload=future_card,
        signer_did=future_card.owner_did,
    )
    rejected_card = _memory_card("mem_export_rejected", "Rejected events must not be exported.")
    rejected_event, _ = create_signed_event(
        event_type="memory_card.created",
        payload=rejected_card,
        signer_did=rejected_card.owner_did,
    )
    conn = connect(config.database_path)
    try:
        initialize(conn)
        insert_signed_event(conn, event=trusted_event, public_key=trusted_public_key)
        insert_signed_event(conn, event=unsupported_event, public_key=unsupported_public_key)
        insert_signed_event(conn, event=rejected_event, public_key=unsupported_public_key)
        conn.commit()
        rows = fetch_all(
            conn,
            """
            select event_hash, raw_event_json, trust_status
            from signed_events
            order by event_hash
            """,
        )
    finally:
        conn.close()
    output_path = tmp_path / "events.jsonl"

    result = export_memory_events(config=config, output_path=output_path, since="2000-01-01")

    raw_by_status = {str(row["trust_status"]): str(row["raw_event_json"]) for row in rows}
    exported = set(output_path.read_text(encoding="utf-8").splitlines())
    assert result.events_exported == 2
    assert exported == {raw_by_status["trusted"], raw_by_status["unsupported"]}
    assert raw_by_status["rejected"] not in exported


def test_memory_export_cli_writes_jsonl(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")
    output_path = tmp_path / "events.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "memory-export",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--since",
            "2000-01-01",
            "--output-path",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "events_exported=1" in result.output
    assert output_path.exists()


def test_memory_export_group_cli_writes_jsonl(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")
    output_path = tmp_path / "events.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "export",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--since",
            "2000-01-01",
            "--output-path",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "events_exported=1" in result.output
    assert output_path.exists()


def test_memory_export_group_cli_uses_config_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "configured-data"
    vault = tmp_path / "configured-vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(f"[paths]\ndata_dir = '{data_dir}'\nobsidian_vault = '{vault}'\n", encoding="utf-8")
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")
    output_path = tmp_path / "configured-events.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "export",
            "--config",
            str(config_path),
            "--since",
            "2000-01-01",
            "--output-path",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "events_exported=1" in result.output
    assert output_path.exists()


def _insert_candidate(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id, date_key
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cand_test_001",
                "用户要求音频本地处理。",
                "requirement",
                json.dumps({"type": "project", "id": "personal_context_node", "label": "Personal Context Node"}),
                0.95,
                json.dumps(
                    [
                        {
                            "evidence_id": "ev_test",
                            "source_type": "transcript_segment",
                            "source_id": "seg_test",
                            "quote": "音频必须本地处理。",
                        }
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "pending_review",
                None,
                "2087-05-10",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _memory_card(card_id: str, claim: str) -> MemoryCard:
    return MemoryCard(
        card_id=card_id,
        owner_did=f"did:key:{card_id}",
        claim_type="decision",
        claim=claim,
        subject=SubjectRef(type="project", id="personal_context_node", label="Personal Context Node"),
        evidence_refs=[
            EvidenceRef(
                evidence_id=f"ev_{card_id}",
                source_type="transcript_segment",
                source_id=f"seg_{card_id}",
                quote=claim,
            )
        ],
    )


def _mark_review_stable(path: Path) -> None:
    stable_time = time.time() - 121
    os.utime(path, (stable_time, stable_time))
