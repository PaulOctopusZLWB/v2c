from __future__ import annotations

import math
import wave
from pathlib import Path

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.storage.sqlite import connect, fetch_all
from personal_context_node.transcription import transcribe_pending_chunks


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


def test_transcribe_pending_chunks_persists_segments_with_chunk_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    preprocess_imported_audio(
        config=config,
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        max_chunk_ms=300,
    )

    result = transcribe_pending_chunks(config=config, asr=MockASRAdapter(text="本地转写结果"))

    assert result.chunks_transcribed == 3
    assert result.segments_created == 3

    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            """
            select chunk_id, start_ms, end_ms, absolute_start_at, absolute_end_at,
                   text, speaker, speaker_cluster_id, asr_backend, model_name,
                   model_version, decode_config_json
            from transcript_segments
            where asr_backend = 'MockASRAdapter'
            order by start_ms
            """,
        )
        chunks = fetch_all(conn, "select chunk_id, source_start_ms, source_end_ms, status from audio_chunks order by source_start_ms")
        audio = fetch_all(conn, "select recorded_at from audio_files")
    finally:
        conn.close()

    assert [row["text"] for row in rows] == ["本地转写结果", "本地转写结果", "本地转写结果"]
    assert [row["speaker"] for row in rows] == ["self", "self", "self"]
    assert [row["speaker_cluster_id"] for row in rows] == ["self", "self", "self"]
    assert rows[0]["model_name"] == "mock-asr"
    assert rows[0]["model_version"] == "test"
    assert rows[0]["decode_config_json"] == '{"language": "zh", "text": "本地转写结果"}'
    assert rows[0]["chunk_id"] == chunks[0]["chunk_id"]
    assert rows[0]["start_ms"] == chunks[0]["source_start_ms"]
    assert rows[0]["absolute_start_at"] == audio[0]["recorded_at"]
    assert rows[-1]["end_ms"] == chunks[-1]["source_end_ms"]
    assert rows[-1]["absolute_end_at"]
    assert all(chunk["status"] == "transcribed" for chunk in chunks)
