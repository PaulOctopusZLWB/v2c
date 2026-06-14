from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.llm_results import daily_context, day_memory_candidates, session_summary
from personal_context_node.storage.sqlite import connect, initialize


def test_session_summary_returns_parsed_content(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_summary(config.database_path, summary_type="session", target_id="ses_1", content={"headline": "hi", "summary": "s"})

    result = session_summary(config=config, session_id="ses_1")

    assert result is not None
    assert result["content"]["headline"] == "hi"


def test_session_summary_missing_returns_none(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
    finally:
        conn.close()
    assert session_summary(config=config, session_id="ghost") is None


def test_daily_context_and_candidates(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_summary(config.database_path, summary_type="daily", target_id="2087-05-10", content={"summary": "day"})
    _insert_candidate(config.database_path, day="2087-05-10")

    ctx = daily_context(config=config, day="2087-05-10")
    candidates = day_memory_candidates(config=config, day="2087-05-10")

    assert ctx is not None and ctx["content"]["summary"] == "day"
    assert [c["candidate_id"] for c in candidates] == ["cand_1"]
    assert candidates[0]["evidence_segment_ids"] == []


def test_candidate_evidence_segment_ids_resolve(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_evidence_ref(config.database_path, evidence_id="ev_1", segment_id="seg_1")
    _insert_candidate(
        config.database_path,
        day="2087-05-10",
        candidate_id="cand_with_evidence",
        evidence_refs=["ev_1"],
    )

    candidates = day_memory_candidates(config=config, day="2087-05-10")

    assert candidates[0]["candidate_id"] == "cand_with_evidence"
    assert candidates[0]["evidence_segment_ids"] == ["seg_1"]


def test_candidate_evidence_segment_ids_empty_when_unresolved(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_candidate(
        config.database_path,
        day="2087-05-10",
        candidate_id="cand_ghost_evidence",
        evidence_refs=["ev_missing"],
    )

    candidates = day_memory_candidates(config=config, day="2087-05-10")

    assert candidates[0]["evidence_segment_ids"] == []


def _insert_summary(database_path: Path, *, summary_type: str, target_id: str, content: dict) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into summaries (summary_id, summary_type, target_type, target_id, prompt_version, model_name, content_json, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"sum_{target_id}", summary_type, summary_type, target_id, "v1", "rule_based", json.dumps(content), "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_candidate(
    database_path: Path,
    *,
    day: str,
    candidate_id: str = "cand_1",
    evidence_refs: list[str] | None = None,
) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into memory_candidates (candidate_id, candidate_claim, claim_type, subject_json, confidence, evidence_refs_json, status, date_key, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (candidate_id, "Paul 喜欢咖啡", "preference", "{}", 0.9, json.dumps(evidence_refs or []), "pending", day, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_evidence_ref(database_path: Path, *, evidence_id: str, segment_id: str) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        # Mirrors persist_segment_evidence_refs: source_id holds the segment id.
        conn.execute(
            "insert into evidence_refs (evidence_id, source_type, source_ref, source_id, owner_id, quote, created_at) values (?, ?, ?, ?, ?, ?, ?)",
            (evidence_id, "transcript_segment", segment_id, segment_id, "did:owner", "quote text", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
