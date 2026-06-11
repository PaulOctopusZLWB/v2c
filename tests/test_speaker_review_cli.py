from __future__ import annotations

import os
import time
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


def test_speaker_review_cli_publishes_and_syncs(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_segment(config.database_path)
    runner = CliRunner()

    publish = runner.invoke(
        app,
        [
            "publish-speaker-review",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--day",
            "2087-05-10",
        ],
    )

    assert publish.exit_code == 0, publish.output
    review_path = config.obsidian_vault / "90_System" / "Speaker_Review" / "2087-05-10.md"
    assert str(review_path) in publish.output
    review_path.write_text(review_path.read_text(encoding="utf-8").replace("- spk_self: self", "- spk_self: Paul"), encoding="utf-8")
    edited_at = time.time() - 121
    os.utime(review_path, (edited_at, edited_at))

    sync = runner.invoke(
        app,
        [
            "sync-speaker-review",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--day",
            "2087-05-10",
        ],
    )

    assert sync.exit_code == 0, sync.output
    assert "mappings_upserted=1" in sync.output


def _insert_segment(database_path: Path) -> None:
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
            insert into sessions (
              session_id, date_key, started_at, ended_at, source,
              segment_count, active_speech_ms, first_segment_id,
              exclude_from_memory, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses_test",
                "2087-05-10",
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:00:01+08:00",
                "derived_from_segments",
                1,
                1000,
                "seg_test",
                0,
                "2087-05-10T00:10:00+08:00",
                "2087-05-10T00:10:00+08:00",
            ),
        )
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_test",
                "aud_test",
                "chk_test",
                "ses_test",
                0,
                1000,
                "本人发言。",
                "zh",
                "spk_self",
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
