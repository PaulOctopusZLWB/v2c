from __future__ import annotations

import math
import wave
from pathlib import Path

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.process_runner import process_once
from personal_context_node.storage.sqlite import connect, fetch_all
from personal_context_node.tasks import process_status_rows


def test_asr_success_enqueues_session_derive_once(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    process_once(
        config=config,
        run_id="run_vad",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )
    process_once(
        config=config,
        run_id="run_asr",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    session_tasks = [
        row for row in process_status_rows(config=config)
        if row["task_type"] == "session_derive"
    ]
    assert len(session_tasks) == 1
    assert session_tasks[0]["target_type"] == "date_key"
    assert session_tasks[0]["target_id"] == "2087-05-10"
    assert session_tasks[0]["status"] == "pending"

    session_result = process_once(
        config=config,
        run_id="run_session",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert session_result.task_type == "session_derive"
    assert session_result.status == "succeeded"
    conn = connect(config.database_path)
    try:
        sessions = fetch_all(conn, "select date_key, segment_count from sessions")
    finally:
        conn.close()
    assert sessions == [{"date_key": "2087-05-10", "segment_count": 2}]
    assert any(
        row["task_type"] == "daily_generate"
        and row["target_id"] == "2087-05-10"
        and row["status"] == "pending"
        for row in process_status_rows(config=config)
    )


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
