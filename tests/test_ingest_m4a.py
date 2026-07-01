from __future__ import annotations

import wave
from pathlib import Path

import pytest
import personal_context_node.ingest as ingest_module

from personal_context_node.config import AppConfig
from personal_context_node.ingest import import_audio_files
from personal_context_node.storage.sqlite import fetch_all


def test_import_audio_files_converts_m4a_to_wav_before_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sample_data"
    source.mkdir()
    source_audio = source / "TX02_MIC013_20250611_190910_orig.m4a"
    source_audio.write_bytes(b"fake-m4a")

    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"

    def fake_normalize_to_wav(*, source_path: Path, target_path: Path, timeout_seconds: float = 3600.0) -> Path:
        with wave.open(str(target_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(b"\x00\x00" * 16_000)
        return target_path

    monkeypatch.setattr(ingest_module, "normalize_to_wav", fake_normalize_to_wav)

    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    assert import_audio_files(config=config, source_dir=source).imported_files == 1

    conn = ingest_module.connect(config.database_path)
    try:
        row = fetch_all(conn, "select local_raw_path, duration_ms from audio_files")[0]
    finally:
        conn.close()

    assert row["local_raw_path"].endswith(".wav")
    assert row["duration_ms"] > 0
