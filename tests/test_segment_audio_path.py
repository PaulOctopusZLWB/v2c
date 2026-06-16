from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from personal_context_node.audio_preprocessing import _read_wav_metadata
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.transcription import segment_audio_path


FRAMERATE = 16_000
SAMPWIDTH = 2
NCHANNELS = 1


def _write_silence_wav(path: Path, duration_ms: int, *, sampwidth: int = SAMPWIDTH) -> None:
    """Synthesize a tiny PCM WAV of silence (16kHz mono). sampwidth=3 mints a 24-bit source,
    matching 88/89 of the real recordings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    nframes = duration_ms * FRAMERATE // 1000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(NCHANNELS)
        wav.setsampwidth(sampwidth)
        wav.setframerate(FRAMERATE)
        wav.writeframes(b"\x00" * (nframes * sampwidth * NCHANNELS))


def _write_float32_wav(path: Path, duration_ms: int) -> None:
    """Synthesize a 32-bit IEEE-float (audio_format=3) WAV — the encoding of the one real source
    (TX01_MIC003_..._orig.wav) that stdlib ``wave`` refuses to open with 'unknown format: 3'."""
    path.parent.mkdir(parents=True, exist_ok=True)
    nframes = duration_ms * FRAMERATE // 1000
    bits, channels = 32, NCHANNELS
    block_align = channels * bits // 8
    data = b"\x00\x00\x00\x00" * (nframes * channels)  # float32 0.0 == silence
    with path.open("wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + len(data)))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<IHHIIHH", 16, 3, channels, FRAMERATE, FRAMERATE * block_align, block_align, bits))
        f.write(b"data")
        f.write(struct.pack("<I", len(data)))
        f.write(data)


def _insert_audio_file(conn, audio_file_id: str, local_raw_path: str, duration_ms: int) -> None:
    conn.execute(
        "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            audio_file_id,
            "DJI Mic 3",
            f"/source/{audio_file_id}.wav",
            1,
            1,
            local_raw_path,
            f"sha256:{audio_file_id}",
            duration_ms,
            "2087-05-10T08:00:00+08:00",
            "2087-05-10T08:00:00+08:00",
            "imported",
        ),
    )


def _insert_segment(
    conn,
    *,
    segment_id: str,
    audio_file_id: str,
    chunk_id: str,
    start_ms: int,
    end_ms: int,
    is_active: int = 1,
) -> None:
    conn.execute(
        "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            segment_id,
            audio_file_id,
            chunk_id,
            "ses_1",
            start_ms,
            end_ms,
            "你好",
            "zh",
            "spk_1",
            "spk_1",
            f"ev_{segment_id}",
            1.0,
            "FunASRParaformerDiarize",
            "paraformer-zh",
            "test",
            is_active,
            "2087-05-10T08:00:00+08:00",
        ),
    )


def _insert_chunk(conn, *, chunk_id: str, audio_file_id: str, local_chunk_path: str, end_ms: int) -> None:
    conn.execute(
        "insert into audio_chunks (chunk_id, audio_file_id, local_work_path, start_ms, end_ms, source_start_ms, source_end_ms, local_chunk_path, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chunk_id, audio_file_id, local_chunk_path, 0, end_ms, 0, end_ms, local_chunk_path, "transcribed"),
    )


def _wav_props(path: Path) -> tuple[int, int]:
    with wave.open(str(path), "rb") as wav:
        return wav.getframerate(), wav.getnframes()


def test_diarized_segment_returns_sliced_wav(tmp_path: Path) -> None:
    """A diarized segment (synthetic chunk_id, no audio_chunks row) is served by
    slicing the source raw wav over [start_ms, end_ms]."""
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    source = tmp_path / "raw" / "source.wav"
    _write_silence_wav(source, duration_ms=10_000)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_audio_file(conn, "aud_diar", str(source), duration_ms=10_000)
        _insert_segment(
            conn,
            segment_id="seg_diar",
            audio_file_id="aud_diar",
            chunk_id="diar_aud_diar_000002000",
            start_ms=2_000,
            end_ms=5_000,
        )
        conn.commit()
    finally:
        conn.close()

    path = segment_audio_path(config=config, segment_id="seg_diar")

    assert path is not None
    assert path.exists()
    assert path == config.data_dir / "audio" / "segments" / "seg_diar.wav"
    framerate, nframes = _wav_props(path)
    assert framerate == FRAMERATE
    # 3000ms slice at 16kHz -> ~48000 frames.
    expected = (5_000 - 2_000) * FRAMERATE // 1000
    assert abs(nframes - expected) <= 1


def test_end_ms_beyond_duration_is_clamped(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    source = tmp_path / "raw" / "short.wav"
    _write_silence_wav(source, duration_ms=1_000)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_audio_file(conn, "aud_short", str(source), duration_ms=1_000)
        _insert_segment(
            conn,
            segment_id="seg_clamp",
            audio_file_id="aud_short",
            chunk_id="diar_aud_short_000000500",
            start_ms=500,
            end_ms=99_000,  # far beyond EOF
        )
        conn.commit()
    finally:
        conn.close()

    path = segment_audio_path(config=config, segment_id="seg_clamp")

    assert path is not None
    framerate, nframes = _wav_props(path)
    assert framerate == FRAMERATE
    # Clamped to EOF: from 500ms to 1000ms -> ~8000 frames, never a crash.
    expected = (1_000 - 500) * FRAMERATE // 1000
    assert abs(nframes - expected) <= 1


def test_chunk_mode_segment_uses_chunk_path(tmp_path: Path) -> None:
    """Regression: a segment WITH a matching audio_chunks row whose local_chunk_path
    exists must still return THAT path (fallback slicing not taken)."""
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    chunk_path = config.work_audio_dir / "2087-05-10" / "chunk.wav"
    _write_silence_wav(chunk_path, duration_ms=1_000)
    # Source raw exists too, so we can prove the chunk path wins over the slice.
    source = tmp_path / "raw" / "source.wav"
    _write_silence_wav(source, duration_ms=10_000)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_audio_file(conn, "aud_chunk", str(source), duration_ms=10_000)
        _insert_chunk(conn, chunk_id="chk_1", audio_file_id="aud_chunk", local_chunk_path=str(chunk_path), end_ms=1_000)
        _insert_segment(
            conn,
            segment_id="seg_chunk",
            audio_file_id="aud_chunk",
            chunk_id="chk_1",
            start_ms=0,
            end_ms=1_000,
        )
        conn.commit()
    finally:
        conn.close()

    path = segment_audio_path(config=config, segment_id="seg_chunk")

    assert path == chunk_path
    # The slice cache must not have been created.
    assert not (config.data_dir / "audio" / "segments" / "seg_chunk.wav").exists()


def test_missing_source_file_returns_none(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    missing = tmp_path / "raw" / "does_not_exist.wav"

    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_audio_file(conn, "aud_gone", str(missing), duration_ms=10_000)
        _insert_segment(
            conn,
            segment_id="seg_gone",
            audio_file_id="aud_gone",
            chunk_id="diar_aud_gone_000001000",
            start_ms=1_000,
            end_ms=2_000,
        )
        conn.commit()
    finally:
        conn.close()

    assert segment_audio_path(config=config, segment_id="seg_gone") is None


def test_cache_reuse_returns_same_path(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    source = tmp_path / "raw" / "source.wav"
    _write_silence_wav(source, duration_ms=10_000)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_audio_file(conn, "aud_reuse", str(source), duration_ms=10_000)
        _insert_segment(
            conn,
            segment_id="seg_reuse",
            audio_file_id="aud_reuse",
            chunk_id="diar_aud_reuse_000002000",
            start_ms=2_000,
            end_ms=4_000,
        )
        conn.commit()
    finally:
        conn.close()

    first = segment_audio_path(config=config, segment_id="seg_reuse")
    assert first is not None
    mtime_first = first.stat().st_mtime_ns

    second = segment_audio_path(config=config, segment_id="seg_reuse")
    assert second == first
    # Idempotent reuse: the cache file was not re-written.
    assert second.stat().st_mtime_ns == mtime_first


def test_float32_source_returns_decodable_slice(tmp_path: Path) -> None:
    """The dataset contains one IEEE-float (format 3) source that stdlib `wave` cannot open;
    the byte-level slicer must still serve a valid float WAV slice (else those segments 404)."""
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    source = tmp_path / "raw" / "float.wav"
    _write_float32_wav(source, duration_ms=10_000)
    with pytest.raises(wave.Error):  # prove stdlib wave really refuses this source
        wave.open(str(source), "rb").close()

    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_audio_file(conn, "aud_float", str(source), duration_ms=10_000)
        _insert_segment(
            conn,
            segment_id="seg_float",
            audio_file_id="aud_float",
            chunk_id="diar_aud_float_000001000",
            start_ms=1_000,
            end_ms=4_000,
        )
        conn.commit()
    finally:
        conn.close()

    path = segment_audio_path(config=config, segment_id="seg_float")

    assert path is not None
    assert path.exists()
    meta = _read_wav_metadata(path)
    assert meta["audio_format"] == 3  # float encoding preserved (browser decodeAudioData handles it)
    assert meta["bits_per_sample"] == 32
    block_align = NCHANNELS * 32 // 8
    nframes = int(meta["data_size"]) // block_align
    expected = (4_000 - 1_000) * FRAMERATE // 1000
    assert abs(nframes - expected) <= 1


def test_24bit_pcm_source_slices(tmp_path: Path) -> None:
    """88/89 real sources are 24-bit PCM; the frame-aligned byte slice must preserve width."""
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    source = tmp_path / "raw" / "pcm24.wav"
    _write_silence_wav(source, duration_ms=10_000, sampwidth=3)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_audio_file(conn, "aud_24", str(source), duration_ms=10_000)
        _insert_segment(
            conn,
            segment_id="seg_24",
            audio_file_id="aud_24",
            chunk_id="diar_aud_24_000002000",
            start_ms=2_000,
            end_ms=5_000,
        )
        conn.commit()
    finally:
        conn.close()

    path = segment_audio_path(config=config, segment_id="seg_24")

    assert path is not None
    meta = _read_wav_metadata(path)
    assert meta["bits_per_sample"] == 24
    nframes = int(meta["data_size"]) // (NCHANNELS * 24 // 8)
    expected = (5_000 - 2_000) * FRAMERATE // 1000
    assert abs(nframes - expected) <= 1


def test_zero_length_window_returns_none(tmp_path: Path) -> None:
    """start_ms == end_ms must yield None (clean 404), never an undecodable empty 44-byte WAV
    that would also permanently poison the cache."""
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    source = tmp_path / "raw" / "src.wav"
    _write_silence_wav(source, duration_ms=5_000)

    conn = connect(config.database_path)
    try:
        initialize(conn)
        _insert_audio_file(conn, "aud_zero", str(source), duration_ms=5_000)
        _insert_segment(
            conn,
            segment_id="seg_zero",
            audio_file_id="aud_zero",
            chunk_id="diar_aud_zero_000002000",
            start_ms=2_000,
            end_ms=2_000,
        )
        conn.commit()
    finally:
        conn.close()

    assert segment_audio_path(config=config, segment_id="seg_zero") is None
    # No poisoned cache entry was written.
    assert not (config.data_dir / "audio" / "segments" / "seg_zero.wav").exists()
