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
    vad = EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150)

    # Run vad + first asr pass.
    for run_id in ["run_vad", "run_asr_first"]:
        process_once(config=config, run_id=run_id, vad=vad, asr=MockASRAdapter(text="第一次 ASR"), max_chunk_ms=1000)

    conn = connect(config.database_path)
    try:
        chunk_id = fetch_all(conn, "select chunk_id from audio_chunks")[0]["chunk_id"]
    finally:
        conn.close()
    rerun_task(config=config, task_type="asr", target_type="audio_chunk", target_id=str(chunk_id))

    # After the rerun, finishing-stage tasks (session_derive etc.) that were enqueued by
    # the first asr pass may be claimed before the re-queued asr (new scheduling order
    # prefers finishing a day over more transcription). Drain until asr runs.
    for index in range(10):
        result = process_once(config=config, run_id=f"drain_{index}", vad=vad, asr=MockASRAdapter(text="第二次 ASR"), max_chunk_ms=1000)
        if result.task_type == "asr":
            break

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


def test_asr_rerun_reopens_session_derivation_for_affected_day(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    for run_id, text in [
        ("run_vad", "第一次 ASR"),
        ("run_asr_first", "第一次 ASR"),
        ("run_session_first", "第一次 ASR"),
    ]:
        process_once(
            config=config,
            run_id=run_id,
            vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
            asr=MockASRAdapter(text=text),
            max_chunk_ms=1000,
        )

    conn = connect(config.database_path)
    try:
        chunk_id = fetch_all(conn, "select chunk_id from audio_chunks")[0]["chunk_id"]
    finally:
        conn.close()

    rerun_task(config=config, task_type="asr", target_type="audio_chunk", target_id=str(chunk_id))

    # After the rerun, finishing-stage tasks may be claimed before the re-queued asr
    # (new scheduling order). Drain until asr runs so the fanout resets session_derive.
    vad = EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150)
    for index in range(10):
        result = process_once(config=config, run_id=f"drain_{index}", vad=vad, asr=MockASRAdapter(text="第二次 ASR"), max_chunk_ms=1000)
        if result.task_type == "asr":
            break

    conn = connect(config.database_path)
    try:
        session_tasks = fetch_all(
            conn,
            "select status, retry_count, attempt_count from tasks where task_type = 'session_derive'",
        )
    finally:
        conn.close()

    assert session_tasks == [{"status": "pending", "retry_count": 0, "attempt_count": 0}]


def test_multichunk_file_keeps_all_chunks_active_and_rerun_regenerates_all(tmp_path: Path) -> None:
    # A multi-chunk recording must keep one active segment per chunk (§36.2.5); a
    # per-chunk ASR task must not drop sibling chunks. A file-level ASR rerun
    # regenerates every chunk (§36.2 `--target aud_...`).
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav", seconds=2.0)
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    vad = EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=2000, min_speech_ms=150)
    for index in range(15):
        process_once(config=config, run_id=f"r{index}", vad=vad, asr=MockASRAdapter(text="第一次"), max_chunk_ms=300)

    conn = connect(config.database_path)
    try:
        chunk_count = fetch_all(conn, "select count(*) as c from audio_chunks")[0]["c"]
        active = fetch_all(conn, "select count(*) as c from transcript_segments where is_active = 1")[0]["c"]
        first_chunk = fetch_all(conn, "select chunk_id from audio_chunks order by source_start_ms limit 1")[0]["chunk_id"]
    finally:
        conn.close()
    assert chunk_count >= 2
    assert active == chunk_count  # no sibling chunks dropped

    rerun_task(config=config, task_type="asr", target_type="audio_chunk", target_id=str(first_chunk))
    for index in range(15):
        process_once(config=config, run_id=f"rr{index}", vad=vad, asr=MockASRAdapter(text="第二次"), max_chunk_ms=300)

    conn = connect(config.database_path)
    try:
        active_texts = fetch_all(conn, "select text from transcript_segments where is_active = 1")
    finally:
        conn.close()
    assert len(active_texts) == chunk_count
    assert all(row["text"] == "第二次" for row in active_texts)  # whole file regenerated


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


def test_asr_rerun_keeps_session_id_stable(tmp_path: Path) -> None:
    # §26.2.7 / §36.2.6: an ASR re-run replaces every segment id, but the session id
    # (hence note filename and [[ses_*]] refs) must NOT drift — anchored on the stable
    # first chunk.
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    vad = EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150)
    for index in range(6):
        if process_once(config=config, run_id=f"r{index}", vad=vad, asr=MockASRAdapter(text="first"), max_chunk_ms=1000).status == "no_task":
            break
    conn = connect(config.database_path)
    try:
        before = [row["session_id"] for row in fetch_all(conn, "select session_id from sessions")]
        audio_file_id = fetch_all(conn, "select audio_file_id from audio_files")[0]["audio_file_id"]
    finally:
        conn.close()
    assert len(before) == 1

    rerun_task(config=config, task_type="asr", target_type="audio_file", target_id=str(audio_file_id))
    for index in range(6):
        if process_once(config=config, run_id=f"rr{index}", vad=vad, asr=MockASRAdapter(text="second"), max_chunk_ms=1000).status == "no_task":
            break

    conn = connect(config.database_path)
    try:
        after = [row["session_id"] for row in fetch_all(conn, "select session_id from sessions")]
        active_texts = {row["text"] for row in fetch_all(conn, "select text from transcript_segments where is_active = 1")}
    finally:
        conn.close()
    assert after == before  # session id stable across ASR rerun
    assert active_texts == {"second"}  # transcript fully regenerated
