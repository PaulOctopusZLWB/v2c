from __future__ import annotations

import json
import math
import wave
from pathlib import Path

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.asr import ASRResult, ASRSegment
from personal_context_node.pipeline import run_first_milestone
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
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
    _write_voice_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
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
                   model_version, decode_config_json, is_active
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
    assert [row["is_active"] for row in rows] == [1, 1, 1]
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


def test_mock_asr_default_output_comes_from_fixture(tmp_path: Path) -> None:
    audio_path = tmp_path / "chunk.wav"
    _write_voice_wav(audio_path)
    fixture_path = Path("src/personal_context_node/fixtures/mock_asr_transcript.json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    result = MockASRAdapter().transcribe(audio_path)

    assert result.segments[0].text == fixture["text"]
    assert result.language == fixture["language"]


def test_transcribe_pending_chunks_persists_asr_segment_tags(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_voice_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)
    preprocess_imported_audio(
        config=config,
        vad=EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150),
        max_chunk_ms=300,
    )

    result = transcribe_pending_chunks(config=config, asr=TaggedASRAdapter())

    assert result.segments_created == 3
    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            """
            select text, asr_tags_json
            from transcript_segments
            where asr_backend = 'TaggedASRAdapter'
            order by start_ms
            """,
        )
    finally:
        conn.close()
    assert rows[0] == {"text": "Yeah.", "asr_tags_json": '["yue", "EMO_UNKNOWN", "Speech", "withitn"]'}


class TaggedASRAdapter:
    model_name = "tagged-asr"
    model_version = "test"

    def transcribe(self, audio_path: Path) -> ASRResult:
        return ASRResult(
            segments=[
                ASRSegment(
                    text="Yeah.",
                    start_ms=0,
                    end_ms=300,
                    language="zh",
                    tags=["yue", "EMO_UNKNOWN", "Speech", "withitn"],
                )
            ],
            backend=self.__class__.__name__,
            model_name=self.model_name,
            model_version=self.model_version,
        )


def test_transcribe_works_with_relative_data_dir(tmp_path: Path, monkeypatch) -> None:
    # §32 default data_dir is relative ("data") and §33 `process run --mock` has no
    # --config. local_chunk_path is already the full work path, so transcription must
    # read it directly; re-prefixing a RELATIVE data_dir doubled it (data/data/...).
    monkeypatch.chdir(tmp_path)
    source = Path("recordings")
    _write_voice_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    config = AppConfig(data_dir=Path("data"), obsidian_vault=tmp_path / "vault")
    run_first_milestone(config=config, source_dir=source, confirm_first_candidate=False)

    vad = EnergyVadAdapter(frame_ms=50, threshold=0.05, merge_gap_ms=100, min_speech_ms=150)
    preprocess_imported_audio(config=config, vad=vad, max_chunk_ms=1000)
    result = transcribe_pending_chunks(config=config, asr=MockASRAdapter(text="relative ok"))

    assert result.chunks_transcribed >= 1
    assert result.segments_created >= 1
    conn = connect(config.database_path)
    try:
        active = fetch_all(conn, "select text from transcript_segments where is_active = 1")
    finally:
        conn.close()
    assert any(row["text"] == "relative ok" for row in active)


# ---------------------------------------------------------------------------
# bulk_segment_audio_info: one-connection batched sibling of segment_audio_path.


def _bulkinfo_write_pcm_wav(path: Path, *, ms: int = 3000, rate: int = 16000) -> None:
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x00" * (rate * ms // 1000))


def _bulkinfo_seed(
    config: AppConfig,
    *,
    raw_path: Path,
    chunk_path: Path | None,
    segments: list[tuple[str, int, int]],  # (segment_id, start_ms, end_ms)
    audio_file_id: str = "aud_bulk",
) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (audio_file_id, "DJI Mic 3", "/src/a.wav", str(raw_path), f"sha:{audio_file_id}", 3000, "2026-07-01T08:00:00+08:00", "2026-07-01T09:00:00+08:00", "imported"),
        )
        if chunk_path is not None:
            conn.execute(
                "insert into audio_chunks (chunk_id, audio_file_id, source_start_ms, source_end_ms, local_chunk_path, status) values ('chk_real', ?, 0, 1000, ?, 'transcribed')",
                (audio_file_id, str(chunk_path)),
            )
        for segment_id, start_ms, end_ms in segments:
            chunk_id = "chk_real" if chunk_path is not None and segment_id.endswith("_chunked") else f"chk_{segment_id}"
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, absolute_end_at, text, language, speaker, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, null, ?, ?, ?, ?, ?, 'zh', 'self', ?, 1.0, 'Mock', 'mock', 'test', 1, ?)",
                (segment_id, audio_file_id, chunk_id, start_ms, end_ms, "2026-07-01T08:00:00+08:00", "2026-07-01T08:00:01+08:00", f"text {segment_id}", f"ev_{segment_id}", "2026-07-01T08:00:02+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def test_bulk_segment_audio_info_resolves_chunk_and_fallback_paths(tmp_path: Path) -> None:
    from personal_context_node.transcription import bulk_segment_audio_info

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw = tmp_path / "raw" / "a.wav"
    chunk = tmp_path / "work" / "chunk.wav"
    _bulkinfo_write_pcm_wav(raw, ms=3000)
    _bulkinfo_write_pcm_wav(chunk, ms=1000)
    _bulkinfo_seed(
        config,
        raw_path=raw,
        chunk_path=chunk,
        segments=[("seg_chunked", 0, 800), ("seg_diar", 500, 2500)],
    )

    info = bulk_segment_audio_info(config=config, segment_ids=["seg_chunked", "seg_diar"])

    # Chunk-backed segment resolves to the whole chunk file, and duration_ms is the ACTUAL
    # audio length from the WAV header (1000ms chunk), NOT the 800ms segment window — the
    # zero-padding batch bucketing needs the length the model will really see.
    assert info["seg_chunked"] == (chunk, 1000)
    # Diarize-mode segment (no matching chunk) falls back to the idempotent slice cache.
    path, duration_ms = info["seg_diar"]
    assert duration_ms == 2000
    assert path == config.data_dir / "audio" / "segments" / "seg_diar.wav"
    assert path.exists()

    # Second call reuses the slice cache: even if slicing were to break, the path still resolves.
    again = bulk_segment_audio_info(config=config, segment_ids=["seg_diar"])
    assert again["seg_diar"] == (path, 2000)


def test_bulk_segment_audio_info_single_connection(tmp_path: Path, monkeypatch) -> None:
    import personal_context_node.transcription as tr

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw = tmp_path / "raw" / "a.wav"
    _bulkinfo_write_pcm_wav(raw, ms=3000)
    _bulkinfo_seed(
        config,
        raw_path=raw,
        chunk_path=None,
        segments=[(f"seg_{i}", i * 100, i * 100 + 100) for i in range(20)],
    )

    real_connect = tr.connect
    counter = {"n": 0}

    def counting_connect(path):
        counter["n"] += 1
        return real_connect(path)

    monkeypatch.setattr(tr, "connect", counting_connect)
    info = tr.bulk_segment_audio_info(config=config, segment_ids=[f"seg_{i}" for i in range(20)])
    assert len(info) == 20
    assert counter["n"] == 1  # ONE connection for the whole pass (vs one per segment before)


def test_bulk_segment_audio_info_omits_unsafe_and_unresolvable(tmp_path: Path) -> None:
    from personal_context_node.transcription import bulk_segment_audio_info

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw = tmp_path / "raw" / "a.wav"
    _bulkinfo_write_pcm_wav(raw, ms=3000)
    _bulkinfo_seed(config, raw_path=raw, chunk_path=None, segments=[("seg_ok", 0, 1000), ("seg_zero", 500, 500)])
    # A second file whose raw source does NOT exist -> its segment is unresolvable.
    _bulkinfo_seed(
        config,
        raw_path=tmp_path / "raw" / "missing.wav",
        chunk_path=None,
        segments=[("seg_gone", 0, 1000)],
        audio_file_id="aud_gone",
    )

    info = bulk_segment_audio_info(
        config=config, segment_ids=["seg_ok", "seg_zero", "seg_gone", "../evil", "seg_unknown"]
    )
    # Only the resolvable, safe id survives: zero-length window, missing source, unsafe id and
    # unknown id are all simply absent (caller treats absence like segment_audio_path -> None).
    assert set(info) == {"seg_ok"}
