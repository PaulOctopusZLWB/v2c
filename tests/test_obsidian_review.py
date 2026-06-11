from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_publish_and_confirm_checked_memory_candidates(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", owner_did="did:key:test-owner")
    _insert_candidate(config.database_path)

    review_path = publish_candidate_review(config=config, day="2087-05-10")

    assert review_path == config.obsidian_vault / "30_Memory_Candidates" / "2087-05-10.md"
    text = review_path.read_text(encoding="utf-8")
    assert "- [ ] cand_test_001 | requirement | 用户要求音频本地处理。" in text

    review_path.write_text(text.replace("- [ ] cand_test_001", "- [x] cand_test_001"), encoding="utf-8")
    result = confirm_checked_candidates(config=config, day="2087-05-10")

    assert result.candidates_confirmed == 1
    assert result.signed_events_created == 1

    conn = connect(config.database_path)
    try:
        candidates = fetch_all(conn, "select status, memory_card_id from memory_candidates")
        events = fetch_all(conn, "select event_type, verified from signed_events")
    finally:
        conn.close()

    assert candidates[0]["status"] == "confirmed"
    assert candidates[0]["memory_card_id"].startswith("mem_")
    assert events == [{"event_type": "memory_card.confirmed.v1", "verified": 1}]


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
