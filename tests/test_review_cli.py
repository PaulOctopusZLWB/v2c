from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


def test_review_cli_publishes_and_confirms_checked_candidate(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_candidate(config.database_path)
    runner = CliRunner()

    publish_result = runner.invoke(
        app,
        [
            "publish-review",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--day",
            "2087-05-10",
        ],
    )

    assert publish_result.exit_code == 0, publish_result.output
    review_path = config.obsidian_vault / "30_Memory_Candidates" / "2087-05-10.md"
    assert str(review_path) in publish_result.output
    text = review_path.read_text(encoding="utf-8")
    review_path.write_text(text.replace("- [ ] cand_test_001", "- [x] cand_test_001"), encoding="utf-8")

    confirm_result = runner.invoke(
        app,
        [
            "confirm-review",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--day",
            "2087-05-10",
        ],
    )

    assert confirm_result.exit_code == 0, confirm_result.output
    assert "candidates_confirmed=1" in confirm_result.output
    assert "signed_events_created=1" in confirm_result.output


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
