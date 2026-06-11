from __future__ import annotations

from pathlib import Path

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.config import AppConfig
from personal_context_node.process_runner import process_once
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.tasks import enqueue_task, process_status_rows


def test_process_runner_generates_daily_and_publishes_obsidian(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_and_transcript(config.database_path)
    enqueue_task(config=config, task_type="daily_generate", target_type="date_key", target_id="2087-05-10")

    daily = process_once(
        config=config,
        run_id="run_daily",
        vad=EnergyVadAdapter(),
        asr=MockASRAdapter(),
    )

    assert daily.task_type == "daily_generate"
    assert daily.status == "succeeded"
    assert any(
        row["task_type"] == "obsidian_publish"
        and row["target_id"] == "2087-05-10"
        and row["status"] == "pending"
        for row in process_status_rows(config=config)
    )

    publish = process_once(
        config=config,
        run_id="run_publish",
        vad=EnergyVadAdapter(),
        asr=MockASRAdapter(),
    )

    assert publish.task_type == "obsidian_publish"
    assert publish.status == "succeeded"
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
        conn.commit()
    finally:
        conn.close()
