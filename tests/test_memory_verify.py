from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_memory_verify_rechecks_stored_signed_events(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    confirm_checked_candidates(config=config, day="2087-05-10")

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 1
    assert result.invalid_events == 0
    assert result.materialization_mismatches == 0


def test_confirmed_candidate_materializes_memory_card(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")

    confirm_checked_candidates(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        cards = fetch_all(conn, "select claim, claim_type, status, source_event_hash from memory_cards")
    finally:
        conn.close()

    assert cards == [
        {
            "claim": "用户要求音频本地处理。",
            "claim_type": "requirement",
            "status": "active",
            "source_event_hash": cards[0]["source_event_hash"],
        }
    ]
    assert cards[0]["source_event_hash"].startswith("sha256:")


def test_memory_verify_detects_materialized_card_mismatch(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    confirm_checked_candidates(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        conn.execute("update memory_cards set claim = ?", ("篡改后的 materialized claim",))
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 1
    assert result.invalid_events == 0
    assert result.materialization_mismatches == 1


def test_memory_verify_detects_tampered_payload(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    confirm_checked_candidates(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        event = fetch_all(conn, "select event_id, payload_json from signed_events")[0]
        payload = json.loads(event["payload_json"])
        payload["claim"] = "篡改后的 claim"
        conn.execute(
            "update signed_events set payload_json = ? where event_id = ?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True), event["event_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 1
    assert result.valid_events == 0
    assert result.invalid_events == 1


def test_memory_verify_detects_broken_owner_hash_chain(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path, candidate_id="cand_test_001", claim="用户要求音频本地处理。")
    _insert_candidate(config.database_path, candidate_id="cand_test_002", claim="用户决定保留本地证据链。")
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    confirm_checked_candidates(config=config, day="2087-05-10")

    conn = connect(config.database_path)
    try:
        second = fetch_all(
            conn,
            "select event_hash from signed_events where owner_sequence = 2",
        )[0]
        conn.execute(
            "update signed_events set prev_event_hash = ? where event_hash = ?",
            ("sha256:broken", second["event_hash"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = verify_memory_events(config=config)

    assert result.total_events == 2
    assert result.valid_events == 1
    assert result.invalid_events == 1


def _insert_candidate(
    database_path: Path,
    *,
    candidate_id: str = "cand_test_001",
    claim: str = "用户要求音频本地处理。",
) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into memory_candidates (
              candidate_id, candidate_claim, claim_type, subject_json,
              confidence, evidence_refs_json, status, memory_card_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                claim,
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
            ),
        )
        conn.commit()
    finally:
        conn.close()
