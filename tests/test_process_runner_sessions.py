from __future__ import annotations

import math
import wave
from pathlib import Path

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import DailyContext, SessionSummary
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.process_runner import process_once
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.tasks import enqueue_task, process_status_rows


class RecordingSessionLLM:
    def __init__(self) -> None:
        self.session_segments: list[dict[str, object]] = []

    def generate_daily_context(self, *, day: str, transcript_segments: list[dict[str, object]]) -> DailyContext:
        raise AssertionError("summarize_session should not request daily context")

    def generate_session_summary(self, *, session_id: str, transcript_segments: list[dict[str, object]]) -> SessionSummary:
        self.session_segments = transcript_segments
        return SessionSummary(
            session_id=session_id,
            headline="模拟 LLM session headline",
            summary="模拟 LLM session summary",
            topics=["本地处理"],
            decisions=[],
            todos=[],
            open_questions=[],
        )


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
    assert session_tasks[0]["target_id"] == "2025-06-10"
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
    assert sessions == [{"date_key": "2025-06-10", "segment_count": 1}]
    assert any(
        row["task_type"] == "summarize_session"
        and row["target_type"] == "session"
        and row["status"] == "pending"
        for row in process_status_rows(config=config)
    )


def test_process_once_session_derive_uses_configured_gap_minutes(tmp_path: Path) -> None:
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        session_gap_minutes=40,
    )
    _insert_audio_with_active_segments(
        config=config,
        segments=[
            ("seg_1", 0, 10_000),
            ("seg_2", 30 * 60 * 1000, 30 * 60 * 1000 + 10_000),
        ],
    )
    enqueue_task(config=config, task_type="session_derive", target_type="date_key", target_id="2087-05-10")

    result = process_once(
        config=config,
        run_id="run_session",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="本地任务转写"),
        max_chunk_ms=1000,
    )

    assert result.task_type == "session_derive"
    conn = connect(config.database_path)
    try:
        sessions = fetch_all(conn, "select segment_count from sessions")
    finally:
        conn.close()
    assert sessions == [{"segment_count": 2}]


def test_summarize_session_success_fans_in_to_daily_generate(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    for run_id in ["run_vad", "run_asr", "run_session"]:
        process_once(
            config=config,
            run_id=run_id,
            vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
            asr=MockASRAdapter(text="我决定继续接入真实 ASR，需要保持音频本地处理。"),
            max_chunk_ms=1000,
        )

    summary_result = process_once(
        config=config,
        run_id="run_summary",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="我决定继续接入真实 ASR，需要保持音频本地处理。"),
        max_chunk_ms=1000,
    )

    assert summary_result.task_type == "summarize_session"
    assert summary_result.status == "succeeded"
    assert any(
        row["task_type"] == "daily_generate"
        and row["target_id"] == "2025-06-10"
        and row["status"] == "pending"
        for row in process_status_rows(config=config)
    )


def test_process_once_session_summary_uses_injected_llm_adapter(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    for run_id in ["run_vad", "run_asr", "run_session"]:
        process_once(
            config=config,
            run_id=run_id,
            vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
            asr=MockASRAdapter(text="我决定继续接入真实 ASR，需要保持音频本地处理。"),
            max_chunk_ms=1000,
        )
    llm = RecordingSessionLLM()

    summary_result = process_once(
        config=config,
        run_id="run_summary_fake_llm",
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        asr=MockASRAdapter(text="我决定继续接入真实 ASR，需要保持音频本地处理。"),
        llm=llm,
        max_chunk_ms=1000,
    )

    assert summary_result.task_type == "summarize_session"
    assert summary_result.status == "succeeded"
    assert llm.session_segments
    assert "wav" not in str(llm.session_segments).lower()
    conn = connect(config.database_path)
    try:
        summaries = fetch_all(conn, "select content_json, prompt_version from summaries where summary_type = 'session'")
    finally:
        conn.close()
    assert "模拟 LLM session headline" in summaries[0]["content_json"]
    assert summaries[0]["prompt_version"] == "llm_port.session_summary.v1"


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


def _insert_audio_with_active_segments(*, config: AppConfig, segments: list[tuple[str, int, int]]) -> None:
    conn = connect(config.database_path)
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
                2_000_000,
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T08:00:00+08:00",
                "imported",
            ),
        )
        for segment_id, start_ms, end_ms in segments:
            conn.execute(
                """
                insert into transcript_segments (
                  segment_id, audio_file_id, chunk_id, start_ms, end_ms, text,
                  language, speaker, evidence_id, confidence, asr_backend, model_name, model_version, is_active
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    "aud_test",
                    f"chk_{segment_id}",
                    start_ms,
                    end_ms,
                    "测试片段",
                    "zh",
                    "self",
                    f"ev_{segment_id}",
                    0.99,
                    "MockASRAdapter",
                    "mock-asr",
                    "test",
                    1,
                ),
            )
        conn.commit()
    finally:
        conn.close()
