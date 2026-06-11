from __future__ import annotations

import wave
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all


def test_ingest_scan_cli_lists_wav_candidates(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")

    result = CliRunner().invoke(app, ["ingest-scan", "--source-dir", str(source)])

    assert result.exit_code == 0, result.output
    assert "files_found=1" in result.output
    assert "TX02_MIC001_20870510_173550_orig.wav" in result.output


def test_ingest_import_cli_imports_audio_and_enqueues_vad(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    result = CliRunner().invoke(
        app,
        [
            "ingest-import",
            "--source-dir",
            str(source),
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "imported_files=1" in result.output
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(conn, "select source_path, status from audio_files")
        tasks = fetch_all(conn, "select task_type, target_type, status from tasks")
    finally:
        conn.close()
    assert audio_files == [{"source_path": str(source / "TX02_MIC001_20870510_173550_orig.wav"), "status": "imported"}]
    assert tasks == [{"task_type": "vad", "target_type": "audio_file", "status": "pending"}]


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)
