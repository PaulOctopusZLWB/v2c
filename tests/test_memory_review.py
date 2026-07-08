from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
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


def _seed_one(config: AppConfig, *, candidate_id: str) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id, date_key, created_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                "又一条待确认主张。",
                "fact",
                json.dumps({"type": "project", "id": "personal_context_node", "label": "Personal Context Node"}),
                0.9,
                json.dumps(
                    [{"evidence_id": "ev_after", "source_type": "transcript_segment", "source_id": "seg_after", "quote": "又一条。"}],
                    ensure_ascii=False,
                ),
                "pending_review",
                None,
                "2087-05-11",
                "2087-05-11T00:00:00+08:00",
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


def test_concurrent_confirm_does_not_fork_the_signing_chain(tmp_path: Path) -> None:
    """两次并发 confirm 同一候选:锁串行化 → 恰好一次成功签名,另一次干净失败,
    签名链不劈叉(否则两个同 owner_sequence 的事件会被互相判 rejected,永久锁死)。"""
    config = _config(tmp_path)
    _seed(config, count=1)

    def run() -> object:
        try:
            return confirm_candidate(config=config, candidate_id="cand_0")
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in [pool.submit(run), pool.submit(run)]]

    receipts = [r for r in results if not isinstance(r, ValueError)]
    failures = [r for r in results if isinstance(r, ValueError)]
    assert len(receipts) == 1 and len(failures) == 1  # exactly one won

    conn = connect(config.database_path)
    try:
        # Exactly one signed event, and it materialized (not trust-rejected by a fork).
        events = fetch_all(conn, "select event_type, trust_status from signed_events")
        active = fetch_all(conn, "select card_id from active_memory_cards")
    finally:
        conn.close()
    assert len(events) == 1
    assert events[0]["trust_status"] == "trusted"
    assert len(active) == 1

    # A subsequent confirm of a NEW candidate still signs cleanly (chain not wedged).
    _seed_one(config, candidate_id="cand_after")
    receipt = confirm_candidate(config=config, candidate_id="cand_after")
    assert receipt.card_id.startswith("mem_")


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
