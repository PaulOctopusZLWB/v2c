from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.memory_review import (
    confirm_candidate,
    defer_candidate,
    list_candidates,
    reject_candidate,
    restore_candidate,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.web.app import create_app


def _seed(config: AppConfig, *, count: int = 2) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for i in range(count):
            conn.execute(
                """
                insert into memory_candidates (
                  candidate_id, candidate_claim, claim_type, subject_json,
                  confidence, evidence_refs_json, status, memory_card_id, date_key, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"cand_{i}",
                    f"候选主张 {i}:音频必须本地处理。",
                    "preference" if i % 2 == 0 else "fact",
                    json.dumps({"type": "project", "id": "personal_context_node", "label": "Personal Context Node"}),
                    0.9,
                    json.dumps(
                        [
                            {
                                "evidence_id": f"ev_{i}",
                                "source_type": "transcript_segment",
                                "source_id": f"seg_{i}",
                                "quote": "音频必须本地处理。",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                    "pending_review",
                    None,
                    "2087-05-10",
                    f"2087-05-10T0{i}:00:00+08:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _config(tmp_path: Path) -> AppConfig:
    vault = tmp_path / "vault"
    (vault / ".pcn-vault").mkdir(parents=True, exist_ok=True)
    return AppConfig(data_dir=tmp_path / "data", obsidian_vault=vault)


def test_list_candidates_pending_first_with_did(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed(config)
    payload = list_candidates(config=config)
    assert payload["pending"] == 2 and payload["total"] == 2
    assert str(payload["did"]).startswith("did:")
    first = payload["candidates"][0]
    assert first["claim_type"] in ("preference", "fact")
    assert first["evidence"][0]["segment_id"].startswith("seg_")
    assert first["evidence"][0]["quote"] == "音频必须本地处理。"


def test_confirm_signs_updates_and_writes_vault_note(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed(config)

    receipt = confirm_candidate(config=config, candidate_id="cand_0")

    assert receipt.card_id.startswith("mem_")
    assert receipt.event_type == "memory_card.created"
    assert receipt.signature  # Ed25519 sig present
    assert receipt.note_path and receipt.note_path.endswith("2087-05-10.md")
    assert Path(receipt.note_path).exists()

    conn = connect(config.database_path)
    try:
        row = fetch_all(conn, "select status, memory_card_id from memory_candidates where candidate_id = 'cand_0'")[0]
        events = fetch_all(conn, "select event_type from signed_events")
    finally:
        conn.close()
    assert row["status"] == "confirmed" and row["memory_card_id"] == receipt.card_id
    assert any(e["event_type"] == "memory_card.created" for e in events)

    # 已确认的不可重复确认,也不可 restore(签名链是 append-only)。
    with pytest.raises(ValueError):
        confirm_candidate(config=config, candidate_id="cand_0")
    with pytest.raises(ValueError):
        restore_candidate(config=config, candidate_id="cand_0")


def test_reject_defer_restore_cycle(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed(config)

    reject_candidate(config=config, candidate_id="cand_0")
    defer_candidate(config=config, candidate_id="cand_1")
    payload = list_candidates(config=config)
    assert payload["pending"] == 0

    restore_candidate(config=config, candidate_id="cand_0")
    restore_candidate(config=config, candidate_id="cand_1")
    assert list_candidates(config=config)["pending"] == 2

    with pytest.raises(ValueError):
        reject_candidate(config=config, candidate_id="nope")


def test_memory_routes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed(config)
    client = TestClient(create_app(config=config))

    listing = client.get("/api/memory/candidates")
    assert listing.status_code == 200, listing.text
    assert listing.json()["pending"] == 2

    confirmed = client.post("/api/memory/cand_0/confirm", json={"edited_claim": "音频与转写全程本地。"})
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["card_id"].startswith("mem_") and body["signature"]

    rejected = client.post("/api/memory/cand_1/reject")
    assert rejected.status_code == 200
    restored = client.post("/api/memory/cand_1/restore")
    assert restored.status_code == 200

    bad = client.post("/api/memory/cand_0/confirm")
    assert bad.status_code == 400  # already confirmed
