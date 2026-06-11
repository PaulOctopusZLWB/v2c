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
from personal_context_node.tasks import rerun_task


def test_session_derivation_uses_latest_active_asr_segments(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    for run_id in ["run_vad", "run_asr", "run_session"]:
        process_once(
            config=config,
            run_id=run_id,
            vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
            asr=MockASRAdapter(text="新 active ASR"),
            max_chunk_ms=1000,
        )

    conn = connect(config.database_path)
    try:
        sessions = fetch_all(conn, "select segment_count from sessions")
        active_segments = fetch_all(conn, "select text from transcript_segments where is_active = 1 order by created_at")
    finally:
        conn.close()

    assert sessions == [{"segment_count": 1}]
    assert active_segments == [{"text": "新 active ASR"}]


def test_explicit_asr_rerun_writes_new_active_segments_without_deleting_history(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    process_once(
        config=config,
        run_id="run_vad",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="第一次 ASR"),
        max_chunk_ms=1000,
    )
    first_asr = process_once(
        config=config,
        run_id="run_asr_first",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="第一次 ASR"),
        max_chunk_ms=1000,
    )
    assert first_asr.task_type == "asr"

    conn = connect(config.database_path)
    try:
        chunk_id = fetch_all(conn, "select chunk_id from audio_chunks")[0]["chunk_id"]
    finally:
        conn.close()
    rerun_task(config=config, task_type="asr", target_type="audio_chunk", target_id=str(chunk_id))

    second_asr = process_once(
        config=config,
        run_id="run_asr_second",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="第二次 ASR"),
        max_chunk_ms=1000,
    )

    assert second_asr.task_type == "asr"
    conn = connect(config.database_path)
    try:
        segments = fetch_all(conn, "select text, is_active from transcript_segments order by created_at")
    finally:
        conn.close()

    assert len(segments) == 3
    assert segments[0]["is_active"] == 0
    assert segments[1:] == [
        {"text": "第一次 ASR", "is_active": 0},
        {"text": "第二次 ASR", "is_active": 1},
    ]


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
