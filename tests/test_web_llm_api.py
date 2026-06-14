from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


def test_session_summary_endpoint_404_when_missing(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))
    assert client.get("/api/llm/sessions/ghost/summary").status_code == 404


def test_daily_endpoint_returns_context_and_candidates(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into summaries (summary_id, summary_type, target_type, target_id, prompt_version, model_name, content_json, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sum_d", "daily", "date_key", "2087-05-10", "v1", "rule_based", json.dumps({"summary": "day"}), "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            "insert into evidence_refs (evidence_id, source_type, source_ref, source_id, owner_id, quote, created_at) values (?, ?, ?, ?, ?, ?, ?)",
            ("ev_1", "transcript_segment", "seg_1", "seg_1", "did:owner", "quote text", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            "insert into memory_candidates (candidate_id, candidate_claim, claim_type, subject_json, confidence, evidence_refs_json, status, date_key, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("cand_1", "Paul 喜欢咖啡", "preference", "{}", 0.9, json.dumps(["ev_1"]), "pending", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    response = client.get("/api/llm/days/2087-05-10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["context"]["content"]["summary"] == "day"
    assert payload["memory_candidates"][0]["evidence_segment_ids"] == ["seg_1"]
