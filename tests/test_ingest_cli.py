from __future__ import annotations

import re
import struct
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


def test_ingest_fix_metadata_rewrites_bwf_fields(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    audio_path = source / "TX02_MIC013_20870511_190910_orig.wav"
    _write_test_wav_with_bwf_metadata(audio_path)

    result = CliRunner().invoke(
        app,
        [
            "ingest",
            "fix-metadata",
            "--source-dir",
            str(source),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "repaired_files=1" in result.output

    bext_date, bext_time, ixml_date = _read_wav_metadata_tags(audio_path)
    assert bext_date == "2025-06-11"
    assert bext_time == "19:09:10"
    assert ixml_date == "2025-06-11"


def test_ingest_import_repairs_copied_raw_metadata(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    source_audio = source / "TX02_MIC013_20870511_190910_orig.wav"
    _write_test_wav_with_bwf_metadata(source_audio)
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
    local_path = config.raw_audio_dir / "2025-06-11" / "TX02_MIC013_20870511_190910_orig.wav"
    bext_date, bext_time, ixml_date = _read_wav_metadata_tags(local_path)
    assert bext_date == "2025-06-11"
    assert bext_time == "19:09:10"
    assert ixml_date == "2025-06-11"


def test_ingest_import_accepts_ieee_float_wav_duration(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    source_audio = source / "TX02_MIC013_20870511_190910_orig.wav"
    _write_test_wav_with_bwf_metadata(source_audio, audio_format=3)
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
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select duration_ms from audio_files")
    finally:
        conn.close()
    assert rows == [{"duration_ms": 1000}]


def test_recorded_at_parsing_rewrites_broken_2087_timestamp() -> None:
    assert ingest_module._recorded_at_from_name(Path("TX02_MIC013_20870511_190910_orig.wav")) == "2025-06-11T19:09:10+08:00"
    assert ingest_module._recorded_at_from_name(Path("TX01_MIC001_20260607_155539_orig.wav")) == "2026-06-07T15:55:39+08:00"


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


def _write_test_wav_with_bwf_metadata(path: Path, *, audio_format: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bits_per_sample = 32 if audio_format == 3 else 16
    bytes_per_sample = bits_per_sample // 8
    fmt_payload = struct.pack("<HHIIHH", audio_format, 1, 16_000, 16_000 * bytes_per_sample, bytes_per_sample, bits_per_sample)
    data_payload = b"\0" * 16_000 * bytes_per_sample
    bext_payload = (
        b"ver:02.00.06.01".ljust(256, b"\x00")
        + b"MIC 3".ljust(32, b"\x00")
        + b"".ljust(32, b"\x00")
        + b"2087-05-11"
        + b"19:09:10"
    )
    ixml_payload = (
        b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        b"<BWFXML>"
        b"<BWF_ORIGINATION_DATE>2087-05-1119:09:10</BWF_ORIGINATION_DATE>"
        b"<BWF_ORIGINATION_TIME>19:09:10</BWF_ORIGINATION_TIME>"
        b"</BWFXML>"
    )

    chunks = [
        b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload + (b"\x00" if len(fmt_payload) % 2 == 1 else b""),
        b"data" + struct.pack("<I", len(data_payload)) + data_payload + (b"\x00" if len(data_payload) % 2 == 1 else b""),
        b"bext" + struct.pack("<I", len(bext_payload)) + bext_payload,
        b"iXML" + struct.pack("<I", len(ixml_payload)) + ixml_payload,
    ]
    payload = b"".join(chunks)
    file_size = 4 + len(payload)
    with path.open("wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", file_size))
        f.write(b"WAVE")
        f.write(payload)


def _read_wav_metadata_tags(path: Path) -> tuple[str, str, str]:
    bext_date = ""
    bext_time = ""
    ixml_date = ""
    with path.open("rb") as handle:
        if handle.read(4) != b"RIFF":
            return bext_date, bext_time, ixml_date
        handle.seek(8)
        if handle.read(4) != b"WAVE":
            return bext_date, bext_time, ixml_date
        while True:
            header = handle.read(8)
            if len(header) < 8:
                break
            chunk_id, chunk_size = struct.unpack("<4sI", header)
            data = handle.read(chunk_size)
            if chunk_size % 2 == 1:
                handle.seek(1, 1)
            if chunk_id == b"bext" and len(data) >= 338:
                bext_date = data[320:330].decode("ascii", "replace")
                bext_time = data[330:338].decode("ascii", "replace")
            if chunk_id == b"iXML":
                try:
                    xml = data.rstrip(b"\x00").decode("utf-8")
                except UnicodeDecodeError:
                    continue
                match = re.search(r"<BWF_ORIGINATION_DATE>(.*?)</BWF_ORIGINATION_DATE>", xml)
                if match:
                    ixml_date = match.group(1)
    return bext_date, bext_time, ixml_date
