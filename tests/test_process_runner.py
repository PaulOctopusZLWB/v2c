from __future__ import annotations

import math
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.process_runner import process_once
from personal_context_node.tasks import process_status_rows
from personal_context_node.storage.sqlite import connect


def test_process_once_runs_vad_then_asr_tasks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    vad_result = process_once(
        config=config,
        run_id="run_vad",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert vad_result.task_type == "vad"
    assert vad_result.status == "succeeded"
    tasks_after_vad = process_status_rows(config=config)
    assert any(row["task_type"] == "asr" and row["status"] == "pending" for row in tasks_after_vad)

    asr_result = process_once(
        config=config,
        run_id="run_asr",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert asr_result.task_type == "asr"
    assert asr_result.status == "succeeded"
    tasks_after_asr = process_status_rows(config=config)
    assert any(row["task_type"] == "asr" and row["status"] == "succeeded" for row in tasks_after_asr)


def test_process_once_reclaims_expired_task_before_claiming(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    task_id = next(row["task_id"] for row in process_status_rows(config=config) if row["task_type"] == "vad")
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    conn = connect(config.database_path)
    try:
        conn.execute(
            """
            update tasks
            set status = 'claimed',
                claimed_by_run_id = ?,
                claimed_at = ?,
                lease_expires_at = ?,
                updated_at = ?
            where task_id = ?
            """,
            ("crashed-run", expired_at, expired_at, expired_at, task_id),
        )
        conn.commit()
    finally:
        conn.close()

    result = process_once(
        config=config,
        run_id="recovery-run",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert result.task_id == task_id
    assert result.task_type == "vad"
    assert result.status == "succeeded"


def test_process_once_enqueues_downstream_tasks_with_configured_max_retries(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", task_max_retries=2)
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    process_once(
        config=config,
        run_id="run_vad",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    conn = connect(config.database_path)
    try:
        rows = conn.execute("select task_type, max_retries from tasks where task_type = 'asr'").fetchall()
    finally:
        conn.close()
    assert [(row["task_type"], row["max_retries"]) for row in rows] == [("asr", 2)]


def _write_voice_wav(path: Path, seconds: float = 0.7, sample_rate: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for index in range(int(seconds * sample_rate)):
            sample = int(10_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))
