from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


def test_obsidian_publish_group_cli_writes_day_artifacts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_transcript(config.database_path)

    result = CliRunner().invoke(
        app,
        [
            "obsidian",
            "publish",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--date",
            "2087-05-10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "daily_notes_written=1" in result.output
    assert "session_notes_written=1" in result.output
    assert "candidate_review_written=1" in result.output
    assert "speaker_review_written=1" in result.output
    assert (config.obsidian_vault / "10_Daily" / "2087-05-10.md").exists()
    assert (config.obsidian_vault / "20_Conversations" / "2087-05-10" / "ses_test.md").exists()
    assert (config.obsidian_vault / "30_Memory_Candidates" / "2087-05-10.md").exists()
    assert (config.obsidian_vault / "90_System" / "Speaker_Review" / "2087-05-10.md").exists()


def _insert_session_and_transcript(database_path: Path) -> None:
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
              segment_count, active_speech_ms, first_segment_id, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses_test",
                "2087-05-10",
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T08:10:00+08:00",
                "derived_from_segments",
                1,
                1000,
                "seg_test",
                "2087-05-10T09:00:00+08:00",
                "2087-05-10T09:00:00+08:00",
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
                "我决定继续接入真实 ASR，需要保持音频本地处理。",
                "zh",
                "self",
                "ev_test",
                0.99,
                "MockASRAdapter",
                "mock-asr",
                "test",
            ),
        )
        conn.execute(
            """
            insert into summaries (
              summary_id, summary_type, target_type, target_id, prompt_version,
              model_name, content_json, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sum_daily_test",
                "daily",
                "date_key",
                "2087-05-10",
                "llm_port.daily_summary.v1",
                "rule_based",
                json.dumps(
                    {
                        "schema_version": "daily_summary.v1",
                        "headline": "本地处理日报",
                        "summary": "今天验证了本地音频处理链路。",
                        "todos_rollup": [],
                        "decisions_rollup": [],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "2087-05-10T10:00:00+08:00",
                "2087-05-10T10:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
