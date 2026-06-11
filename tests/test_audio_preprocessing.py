from __future__ import annotations

import math
import wave
from pathlib import Path

from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.config import AppConfig
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.storage.sqlite import connect, fetch_all


def _write_wav(path: Path, segments: list[tuple[float, int]], sample_rate: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for seconds, amplitude in segments:
            for index in range(int(seconds * sample_rate)):
                if amplitude == 0:
                    sample = 0
                else:
                    sample = int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))
                frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))


def test_energy_vad_filters_silence_and_merges_nearby_speech(tmp_path: Path) -> None:
    wav_path = tmp_path / "voice.wav"
    _write_wav(
        wav_path,
        [
            (0.30, 0),
            (0.40, 12_000),
            (0.10, 0),
            (0.35, 12_000),
            (0.30, 0),
        ],
    )

    adapter = EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=200, min_speech_ms=200)

    ranges = adapter.detect(wav_path)

    assert len(ranges) == 1
    assert 250 <= ranges[0].start_ms <= 350
    assert 1100 <= ranges[0].end_ms <= 1200


def test_preprocess_imported_audio_persists_ranges_and_chunks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    wav_path = source / "TX02_MIC001_20870510_173550_orig.wav"
    _write_wav(wav_path, [(0.20, 0), (0.50, 10_000), (0.20, 0)])
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    result = preprocess_imported_audio(
        config=config,
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        max_chunk_ms=300,
    )

    assert result.audio_files_processed == 1
    assert result.speech_ranges_created == 1
    assert result.audio_chunks_created == 2

    conn = connect(config.database_path)
    try:
        ranges = fetch_all(conn, "select start_ms, end_ms from speech_ranges")
        chunks = fetch_all(conn, "select source_start_ms, source_end_ms, local_chunk_path from audio_chunks order by source_start_ms")
    finally:
        conn.close()

    assert ranges[0]["start_ms"] >= 150
    assert ranges[0]["end_ms"] <= 750
    assert chunks[0]["source_start_ms"] == ranges[0]["start_ms"]
    assert chunks[-1]["source_end_ms"] == ranges[0]["end_ms"]
    assert all((tmp_path / "data").joinpath(chunk["local_chunk_path"]).exists() for chunk in chunks)
