from __future__ import annotations

import wave
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


def test_segment_audio_returns_chunk_wav(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    chunk_path = config.work_audio_dir / "2087-05-10" / "chunk.wav"
    _write_wav(chunk_path)
    # Store the full work path exactly as VAD/preprocessing writes it (production stores
    # str(work_audio_dir/...), not a data_dir-relative path).
    _insert_segment_with_chunk(config.database_path, chunk_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/audio/segments/seg_1")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content.startswith(b"RIFF")


def test_segment_audio_missing_returns_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))
    assert client.get("/api/audio/segments/ghost").status_code == 404


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)


def _insert_segment_with_chunk(database_path: Path, chunk_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 1000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into audio_chunks (chunk_id, audio_file_id, local_work_path, start_ms, end_ms, source_start_ms, source_end_ms, local_chunk_path, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("chk_1", "aud_test", str(chunk_path), 0, 1000, 0, 1000, str(chunk_path), "transcribed"),
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("seg_1", "aud_test", "chk_1", "ses_1", 0, 1000, "你好", "zh", "self", "self", "ev_1", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
