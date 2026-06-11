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


def _insert_candidate(database_path: Path) -> None:
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
            ),
        )
        conn.commit()
    finally:
        conn.close()
