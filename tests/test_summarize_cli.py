from __future__ import annotations

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_summarize_cli_generates_daily_context_from_transcripts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_transcript(config.database_path)

    result = CliRunner().invoke(
        app,
        [
            "summarize",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--day",
            "2087-05-10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "summaries_created=1" in result.output
    assert "memory_candidates_created=1" in result.output

    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select day from daily_summaries")
    finally:
        conn.close()
    assert summaries == [{"day": "2087-05-10"}]


def _insert_transcript(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_test",
                "DJI Mic 3",
                "/source.wav",
                "/local.wav",
                "sha256:test",
                1000,
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:10:00+08:00",
                "imported",
            ),
        )
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_test",
                "aud_test",
                "chk_test",
                0,
                1000,
                "我决定继续接入真实 ASR。",
                "zh",
                "self",
                "ev_test",
                0.99,
                "MockASRAdapter",
                "mock-asr",
                "test",
            ),
        )
        conn.commit()
    finally:
        conn.close()
