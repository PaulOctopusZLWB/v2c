from __future__ import annotations

import wave
from pathlib import Path

import personal_context_node.ingest as ingest_module
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


def test_ingest_scan_cli_lists_uppercase_wav_candidates(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "REC001.WAV")

    result = CliRunner().invoke(app, ["ingest-scan", "--source-dir", str(source)])

    assert result.exit_code == 0, result.output
    assert "files_found=1" in result.output
    assert "REC001.WAV" in result.output


def test_ingest_scan_group_cli_lists_wav_candidates(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")

    result = CliRunner().invoke(app, ["ingest", "scan", "--source-dir", str(source)])

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


def test_ingest_import_group_cli_imports_audio_and_enqueues_vad(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    result = CliRunner().invoke(
        app,
        [
            "ingest",
            "import",
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


def test_ingest_import_records_source_file_metadata(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    audio_path = source / "TX02_MIC001_20870510_173550_orig.wav"
    _write_tiny_wav(audio_path)
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
    stat = audio_path.stat()
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(conn, "select source_size_bytes, source_mtime_ns from audio_files")
    finally:
        conn.close()
    assert audio_files == [{"source_size_bytes": stat.st_size, "source_mtime_ns": stat.st_mtime_ns}]


def test_ingest_import_migrates_existing_database_for_source_metadata(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    audio_path = source / "TX02_MIC001_20870510_173550_orig.wav"
    _write_tiny_wav(audio_path)
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        conn.execute(
            """
            create table audio_files (
              audio_file_id text primary key,
              source_device text not null,
              source_path text not null,
              local_raw_path text not null,
              sha256 text not null,
              duration_ms integer not null,
              recorded_at text not null,
              imported_at text not null,
              status text not null,
              unique(source_path, sha256)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

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
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(conn, "select source_size_bytes, source_mtime_ns from audio_files")
    finally:
        conn.close()
    assert audio_files == [{"source_size_bytes": audio_path.stat().st_size, "source_mtime_ns": audio_path.stat().st_mtime_ns}]


def test_ingest_import_skips_unstable_audio_candidate(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    monkeypatch.setattr(ingest_module, "is_file_stable", lambda path: False, raising=False)

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
    assert "imported_files=0" in result.output
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(conn, "select audio_file_id from audio_files")
        tasks = fetch_all(conn, "select task_id from tasks")
    finally:
        conn.close()
    assert audio_files == []
    assert tasks == []


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)
