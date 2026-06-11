from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.memory_export import export_memory_events
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_export_memory_events_writes_trusted_raw_events_jsonl(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
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


def test_memory_export_cli_writes_jsonl(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
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
