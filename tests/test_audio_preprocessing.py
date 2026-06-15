from __future__ import annotations

import json
import math
import struct
import wave
from datetime import datetime, timedelta
from pathlib import Path

from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.adapters.vad.mock import MockVADAdapter
from personal_context_node.audio_preprocessing import (
    _convert_pcm_frames,
    _convert_pcm_frames_blocked,
    _read_wav_metadata,
    _split_range,
    preprocess_imported_audio,
)
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.vad import SpeechRange
from personal_context_node.ingest import import_audio_files
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

    result = adapter.detect(wav_path)

    assert result.backend == "EnergyVadAdapter"
    assert result.backend_version is None
    assert result.config["frame_ms"] == 50
    assert result.warnings == []
    assert len(result.ranges) == 1
    assert 250 <= result.ranges[0].start_ms <= 350
    assert 1100 <= result.ranges[0].end_ms <= 1200


def test_split_range_applies_configured_chunk_overlap() -> None:
    chunks = _split_range(SpeechRange(start_ms=0, end_ms=1000), max_chunk_ms=400, chunk_overlap_ms=100)

    assert chunks == [
        SpeechRange(start_ms=0, end_ms=400),
        SpeechRange(start_ms=300, end_ms=700),
        SpeechRange(start_ms=600, end_ms=1000),
    ]


def test_preprocess_imported_audio_uses_ranges_to_persist_chunks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    wav_path = source / "TX02_MIC001_20870510_173550_orig.wav"
    _write_wav(wav_path, [(0.20, 0), (0.50, 10_000), (0.20, 0)])
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", chunk_overlap_ms=0)
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
        range_tables = fetch_all(conn, "select name from sqlite_master where type = 'table' and name = 'speech_ranges'")
        chunks = fetch_all(
            conn,
            """
            select
              source_start_ms, source_end_ms, start_ms, end_ms,
              local_chunk_path, local_work_path, absolute_start_at, absolute_end_at,
              vad_backend, vad_config_json, created_at
            from audio_chunks
            order by source_start_ms
            """,
        )
        audio = fetch_all(conn, "select recorded_at from audio_files")
    finally:
        conn.close()

    assert range_tables == []
    assert chunks[0]["source_start_ms"] >= 150
    assert chunks[0]["start_ms"] == chunks[0]["source_start_ms"]
    assert chunks[-1]["source_end_ms"] <= 750
    assert chunks[-1]["end_ms"] == chunks[-1]["source_end_ms"]
    assert chunks[0]["local_work_path"] == chunks[0]["local_chunk_path"]
    expected_start = datetime.fromisoformat(audio[0]["recorded_at"]) + timedelta(milliseconds=chunks[0]["start_ms"])
    assert chunks[0]["absolute_start_at"] == expected_start.isoformat()
    assert chunks[-1]["absolute_end_at"]
    assert chunks[0]["vad_backend"] == "EnergyVadAdapter"
    assert chunks[0]["vad_config_json"] == '{"frame_ms": 50, "merge_gap_ms": 100, "min_speech_ms": 150, "threshold": 0.05}'
    assert chunks[0]["created_at"]
    assert all((tmp_path / "data").joinpath(chunk["local_chunk_path"]).exists() for chunk in chunks)
    audit_path = config.work_audio_dir / "2025-06-10" / "TX02_MIC001_20870510_173550_orig.vad.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["audio_file_id"]
    assert audit["source_path"] == str(config.raw_audio_dir / "2025-06-10" / "TX02_MIC001_20870510_173550_orig.wav")
    assert audit["backend"] == "EnergyVadAdapter"
    assert audit["backend_version"] is None
    assert audit["config"] == {"frame_ms": 50, "merge_gap_ms": 100, "min_speech_ms": 150, "threshold": 0.05}
    assert audit["warnings"] == []
    assert len(audit["ranges"]) == 1
    assert audit["ranges"][0]["start_ms"] >= 150
    assert audit["ranges"][0]["end_ms"] <= 750


def test_configured_audio_storage_paths_are_used_by_ingest_and_preprocess(tmp_path: Path) -> None:
    source = tmp_path / "source"
    wav_path = source / "TX02_MIC001_20870510_173550_orig.wav"
    _write_wav(wav_path, [(0.20, 0), (0.50, 10_000), (0.20, 0)])
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{tmp_path / "data"}"
raw_audio_dir = "{tmp_path / "raw-store"}"
work_audio_dir = "{tmp_path / "work-store"}"
sqlite_path = "{tmp_path / "state" / "pcn.sqlite"}"
obsidian_vault = "{tmp_path / "vault"}"
nas_archive_root = "{tmp_path / "nas"}"

[vad]
chunk_overlap_ms = 0
""".strip(),
        encoding="utf-8",
    )
    config = AppConfig.from_toml(config_path)

    import_audio_files(config=config, source_dir=source)
    result = preprocess_imported_audio(
        config=config,
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        max_chunk_ms=300,
    )

    assert result.audio_chunks_created == 2
    conn = connect(config.database_path)
    try:
        audio_file = fetch_all(conn, "select local_raw_path from audio_files")[0]
        chunks = fetch_all(conn, "select local_chunk_path from audio_chunks order by source_start_ms")
    finally:
        conn.close()

    assert Path(audio_file["local_raw_path"]).is_relative_to(config.raw_audio_dir)
    assert all(Path(chunk["local_chunk_path"]).is_relative_to(config.work_audio_dir) for chunk in chunks)
    assert all(Path(chunk["local_chunk_path"]).exists() for chunk in chunks)


def test_preprocess_writes_chunks_using_configured_audio_format(tmp_path: Path) -> None:
    source = tmp_path / "source"
    wav_path = source / "TX02_MIC001_20870510_173550_orig.wav"
    _write_stereo_wav(wav_path, [(0.60, 10_000)], sample_rate=8_000)
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", chunk_overlap_ms=0)

    import_audio_files(config=config, source_dir=source)
    result = preprocess_imported_audio(
        config=config,
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        max_chunk_ms=1_000,
    )

    assert result.audio_chunks_created == 1
    conn = connect(config.database_path)
    try:
        chunk_path = Path(fetch_all(conn, "select local_chunk_path from audio_chunks")[0]["local_chunk_path"])
    finally:
        conn.close()
    with wave.open(str(chunk_path), "rb") as chunk:
        assert chunk.getframerate() == config.audio.target_sample_rate_hz
        assert chunk.getnchannels() == config.audio.target_channels
        assert chunk.getsampwidth() == 2


def test_preprocess_normalizes_ieee_float_wav_chunks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    wav_path = source / "TX01_MIC003_20260607_160317_orig.wav"
    _write_float_wav(wav_path, seconds=1.2, sample_rate=16_000)
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", chunk_overlap_ms=0)

    import_audio_files(config=config, source_dir=source)
    result = preprocess_imported_audio(config=config, vad=MockVADAdapter(), max_chunk_ms=1_000)

    assert result.audio_chunks_created == 1
    conn = connect(config.database_path)
    try:
        chunk_path = Path(fetch_all(conn, "select local_chunk_path from audio_chunks")[0]["local_chunk_path"])
    finally:
        conn.close()
    with wave.open(str(chunk_path), "rb") as chunk:
        assert chunk.getframerate() == config.audio.target_sample_rate_hz
        assert chunk.getnchannels() == config.audio.target_channels
        assert chunk.getsampwidth() == 2
        assert chunk.getnframes() == 16_000


def test_read_wav_metadata_stores_data_offset_not_payload(tmp_path: Path) -> None:
    path = tmp_path / "float.wav"
    _write_ieee_float_wav(path, samples=[0.1, 0.2, 0.3, 0.4], sample_rate=16000, channels=1)

    metadata = _read_wav_metadata(path)

    assert "data" not in metadata
    assert metadata["data_offset"] > 0
    assert metadata["data_size"] == 16


def test_pcm_conversion_matches_existing_output_for_small_blocks() -> None:
    frames = b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in [100, -100, 200, -200])

    whole = _convert_pcm_frames(
        frames,
        source_sample_rate=16000,
        source_channels=1,
        source_sample_width=2,
        target_sample_rate=16000,
        target_channels=1,
        target_sample_width=2,
    )
    blocked = _convert_pcm_frames_blocked(
        frames,
        source_sample_rate=16000,
        source_channels=1,
        source_sample_width=2,
        target_sample_rate=16000,
        target_channels=1,
        target_sample_width=2,
        block_frames=2,
    )

    assert blocked == whole


def test_pcm_blocked_conversion_matches_whole_for_downsampling() -> None:
    # The production path downsamples 48kHz -> 16kHz, so blocked output must equal a whole
    # conversion (global resampling phase, no per-block reset), not just for equal rates.
    frames = b"".join(int(((i % 7) - 3) * 1000).to_bytes(2, "little", signed=True) for i in range(48_000))
    kwargs = dict(
        source_sample_rate=48_000,
        source_channels=1,
        source_sample_width=2,
        target_sample_rate=16_000,
        target_channels=1,
        target_sample_width=2,
    )

    whole = _convert_pcm_frames(frames, **kwargs)
    blocked = _convert_pcm_frames_blocked(frames, block_frames=16_000, **kwargs)

    assert blocked == whole


def test_read_wav_metadata_does_not_read_full_payload(tmp_path: Path, monkeypatch) -> None:
    # Header parsing must not pull the audio payload into memory: the data chunk is large,
    # but metadata reads should stay tiny regardless of file size.
    path = tmp_path / "big_float.wav"
    _write_ieee_float_wav(path, samples=[0.0] * 200_000, sample_rate=16_000, channels=1)

    real_read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("read whole file")))
    try:
        metadata = _read_wav_metadata(path)
    finally:
        monkeypatch.setattr(Path, "read_bytes", real_read)

    assert metadata["data_size"] == 200_000 * 4
    assert metadata["data_offset"] > 0


def _write_ieee_float_wav(path: Path, *, samples: list[float], sample_rate: int, channels: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(struct.pack("<f", sample) for sample in samples)
    byte_rate = sample_rate * channels * 4
    block_align = channels * 4
    fmt = struct.pack("<HHIIHH", 3, channels, sample_rate, byte_rate, block_align, 32)
    payload = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WAVE" + payload)


def _write_stereo_wav(path: Path, segments: list[tuple[float, int]], sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for seconds, amplitude in segments:
            for index in range(int(seconds * sample_rate)):
                sample = int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))
                sample_bytes = sample.to_bytes(2, byteorder="little", signed=True)
                frames.extend(sample_bytes)
                frames.extend(sample_bytes)
        wav.writeframes(bytes(frames))


def _write_float_wav(path: Path, *, seconds: float, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for index in range(int(seconds * sample_rate)):
        sample = 0.5 * math.sin(2 * math.pi * 440 * index / sample_rate)
        frames.extend(struct.pack("<f", sample))
    fmt_chunk = struct.pack("<HHIIHH", 3, 1, sample_rate, sample_rate * 4, 4, 32)
    payload = (
        b"RIFF"
        + struct.pack("<I", 4 + (8 + len(fmt_chunk)) + (8 + len(frames)))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt_chunk))
        + fmt_chunk
        + b"data"
        + struct.pack("<I", len(frames))
        + bytes(frames)
    )
    path.write_bytes(payload)
