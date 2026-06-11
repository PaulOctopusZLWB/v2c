from __future__ import annotations

import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.obsidian_review import confirm_checked_candidates, publish_candidate_review
from personal_context_node.storage.sqlite import connect, initialize


def test_memory_verify_cli_reports_valid_events(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")

    result = CliRunner().invoke(
        app,
        [
            "memory-verify",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "total_events=1" in result.output
    assert "valid_events=1" in result.output
    assert "invalid_events=0" in result.output


def test_memory_verify_group_cli_reports_valid_events(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_candidate(config.database_path)
    review_path = publish_candidate_review(config=config, day="2087-05-10")
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    _mark_review_stable(review_path)
    confirm_checked_candidates(config=config, day="2087-05-10")

    result = CliRunner().invoke(
        app,
        [
            "memory",
            "verify",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "total_events=1" in result.output
    assert "valid_events=1" in result.output
    assert "invalid_events=0" in result.output


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


def _mark_review_stable(path: Path) -> None:
    stable_time = time.time() - 121
    os.utime(path, (stable_time, stable_time))
