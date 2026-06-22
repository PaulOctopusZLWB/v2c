from __future__ import annotations

import os
import re
import stat
import struct
import wave
from pathlib import Path

import personal_context_node.ingest as ingest_module
from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.ingest import import_audio_files
from personal_context_node.storage.sqlite import connect, fetch_all


def test_ingest_scan_cli_lists_wav_candidates(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20250610_173550_orig.wav")

    result = CliRunner().invoke(app, ["ingest-scan", "--source-dir", str(source)])

    assert result.exit_code == 0, result.output
    assert "files_found=1" in result.output
    assert "TX02_MIC001_20250610_173550_orig.wav" in result.output


def test_ingest_scan_cli_lists_uppercase_wav_candidates(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "REC001.WAV")

    result = CliRunner().invoke(app, ["ingest-scan", "--source-dir", str(source)])

    assert result.exit_code == 0, result.output
    assert "files_found=1" in result.output
    assert "REC001.WAV" in result.output


def test_ingest_scan_group_cli_lists_wav_candidates(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20250610_173550_orig.wav")

    result = CliRunner().invoke(app, ["ingest", "scan", "--source-dir", str(source)])

    assert result.exit_code == 0, result.output
    assert "files_found=1" in result.output
    assert "TX02_MIC001_20250610_173550_orig.wav" in result.output


def test_ingest_import_cli_imports_audio_and_enqueues_vad(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
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
    assert audio_files == [{"source_path": str(source / "TX02_MIC001_20250610_173550_orig.wav"), "status": "imported"}]
    assert tasks == [{"task_type": "vad", "target_type": "audio_file", "status": "pending"}]


def test_directory_import_stamps_vad_task_with_recorded_date_priority(tmp_path: Path) -> None:
    # The web "导入" button goes through import_audio_files (the directory-scan path), which must
    # ALSO stamp the per-file vad task with a date-derived priority so the backlog drains earliest
    # day first. Pins the second of the two ingest call sites (the port path is pinned separately);
    # dropping `priority=` here reverts the whole UI-imported backlog to the flat default 100.
    from datetime import date

    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20250610_173550_orig.wav")  # parses to recorded 2025-06-10
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    assert import_audio_files(config=config, source_dir=source).imported_files == 1

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select priority from tasks where task_type = 'vad'")
    finally:
        conn.close()
    expected = (date(2025, 6, 10) - date(2000, 1, 1)).days
    assert expected == 9292
    assert [r["priority"] for r in rows] == [expected]


def test_ingest_import_marks_local_raw_audio_read_only(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    result = import_audio_files(config=config, source_dir=source)

    assert result.imported_files == 1
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(conn, "select local_raw_path from audio_files")
    finally:
        conn.close()
    raw_path = Path(str(audio_files[0]["local_raw_path"]))
    assert stat.S_IMODE(raw_path.stat().st_mode) & 0o222 == 0


def test_ingest_import_group_cli_imports_audio_and_enqueues_vad(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    _write_tiny_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
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
    assert audio_files == [{"source_path": str(source / "TX02_MIC001_20250610_173550_orig.wav"), "status": "imported"}]
    assert tasks == [{"task_type": "vad", "target_type": "audio_file", "status": "pending"}]


def test_ingest_import_group_cli_uses_configured_dji_device_root(tmp_path: Path) -> None:
    source = tmp_path / "mounted_dji"
    _write_tiny_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data_dir}"
obsidian_vault = "{vault}"

[device.dji_mic_3]
root_path = "../mounted_dji"
volume_name_patterns = ["*"]
stable_seconds = 0
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["ingest", "import", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "imported_files=1" in result.output
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(conn, "select source_device, source_path, local_raw_path, status from audio_files")
    finally:
        conn.close()
    raw_path = Path(str(audio_files[0]["local_raw_path"]))
    assert audio_files == [
        {
            "source_device": "DJI Mic 3",
            "source_path": str(source / "TX02_MIC001_20250610_173550_orig.wav"),
            "local_raw_path": str(raw_path),
            "status": "imported",
        }
    ]
    assert stat.S_IMODE(raw_path.stat().st_mode) & 0o222 == 0


def test_ingest_import_group_cli_auto_discovers_no_name_volume(tmp_path: Path) -> None:
    volumes = tmp_path / "Volumes"
    source = volumes / "NO NAME"
    nested_audio = source / "TX_MIC001_20250610_173550" / "TX02_MIC001_20250610_173550_orig.wav"
    trash_audio = source / ".Trashes" / "501" / "TX02_MIC999_20250610_173550_orig.wav"
    _write_tiny_wav(nested_audio)
    _write_tiny_wav(trash_audio)
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data_dir}"
obsidian_vault = "{vault}"

[device.dji_mic_3]
volume_root = "../Volumes"
volume_name_patterns = ["NO NAME"]
audio_globs = ["**/*.wav"]
stable_seconds = 0
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["ingest", "import", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "imported_files=1" in result.output
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(conn, "select source_device, source_path from audio_files")
    finally:
        conn.close()
    assert audio_files == [{"source_device": "DJI Mic 3", "source_path": str(nested_audio)}]


def test_ingest_import_group_cli_uses_configured_audio_globs(tmp_path: Path) -> None:
    source = tmp_path / "mounted_dji"
    nested_audio = source / "REC" / "TX02_MIC001_20250610_173550_orig.wav"
    _write_tiny_wav(nested_audio)
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data_dir}"
obsidian_vault = "{vault}"

[device.dji_mic_3]
root_path = "../mounted_dji"
volume_name_patterns = ["*"]
audio_globs = ["**/*.wav"]
stable_seconds = 0
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["ingest", "import", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "imported_files=1" in result.output
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(conn, "select source_path from audio_files")
    finally:
        conn.close()
    assert audio_files == [{"source_path": str(nested_audio)}]


def test_ingest_import_group_cli_skips_disabled_dji_device(tmp_path: Path) -> None:
    source = tmp_path / "DJI_MIC"
    _write_tiny_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data_dir}"
obsidian_vault = "{vault}"

[device.dji_mic_3]
enabled = false
root_path = "../DJI_MIC"
stable_seconds = 0
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["ingest", "import", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "imported_files=0" in result.output
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(conn, "select audio_file_id from audio_files")
    finally:
        conn.close()
    assert audio_files == []


def test_ingest_import_records_source_file_metadata(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    audio_path = source / "TX02_MIC001_20250610_173550_orig.wav"
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


def test_ingest_import_identity_includes_source_size_mtime_and_hash(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    audio_path = source / "TX02_MIC001_20250610_173550_orig.wav"
    _write_tiny_wav(audio_path)
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    first = import_audio_files(config=config, source_dir=source)
    original_stat = audio_path.stat()
    changed_mtime_ns = original_stat.st_mtime_ns + 1_000_000
    os.utime(audio_path, ns=(changed_mtime_ns, changed_mtime_ns))
    second = import_audio_files(config=config, source_dir=source)

    assert first.imported_files == 1
    assert second.imported_files == 1
    conn = connect(config.database_path)
    try:
        audio_files = fetch_all(
            conn,
            """
            select source_path, source_size_bytes, source_mtime_ns, sha256, local_raw_path
            from audio_files
            order by source_mtime_ns
            """,
        )
        tasks = fetch_all(conn, "select task_type, target_id from tasks order by created_at")
    finally:
        conn.close()
    assert [row["source_path"] for row in audio_files] == [str(audio_path), str(audio_path)]
    assert [row["source_size_bytes"] for row in audio_files] == [original_stat.st_size, original_stat.st_size]
    assert [row["source_mtime_ns"] for row in audio_files] == [original_stat.st_mtime_ns, changed_mtime_ns]
    assert audio_files[0]["sha256"] == audio_files[1]["sha256"]
    assert audio_files[0]["local_raw_path"] != audio_files[1]["local_raw_path"]
    assert Path(str(audio_files[0]["local_raw_path"])).exists()
    assert Path(str(audio_files[1]["local_raw_path"])).exists()
    assert len(tasks) == 2


def test_ingest_fix_metadata_rewrites_bwf_fields(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    audio_path = source / "TX02_MIC013_20250611_190910_orig.wav"
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
    source_audio = source / "TX02_MIC013_20250611_190910_orig.wav"
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
    local_path = config.raw_audio_dir / "2025-06-11" / "TX02_MIC013_20250611_190910_orig.wav"
    bext_date, bext_time, ixml_date = _read_wav_metadata_tags(local_path)
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select sha256 from audio_files")
    finally:
        conn.close()
    assert bext_date == "2025-06-11"
    assert bext_time == "19:09:10"
    assert ixml_date == "2025-06-11"
    assert rows == [{"sha256": ingest_module._sha256(local_path)}]


def test_ingest_import_does_not_duplicate_repaired_source_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    source_audio = source / "TX02_MIC013_20250611_190910_orig.wav"
    _write_test_wav_with_bwf_metadata(source_audio)
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")

    first = import_audio_files(config=config, source_dir=source)
    second = import_audio_files(config=config, source_dir=source)

    assert first.imported_files == 1
    assert second.imported_files == 0
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select count(*) as n from audio_files")
    finally:
        conn.close()
    assert rows == [{"n": 1}]


def test_ingest_import_accepts_ieee_float_wav_duration(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    source_audio = source / "TX02_MIC013_20250611_190910_orig.wav"
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
    assert ingest_module._recorded_at_from_name(Path("TX02_MIC013_20250611_190910_orig.wav")) == "2025-06-11T19:09:10+08:00"
    assert ingest_module._recorded_at_from_name(Path("TX01_MIC001_20260607_155539_orig.wav")) == "2026-06-07T15:55:39+08:00"


def test_ingest_import_migrates_existing_database_for_source_metadata(tmp_path: Path) -> None:
    source = tmp_path / "sample_data"
    audio_path = source / "TX02_MIC001_20250610_173550_orig.wav"
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
    _write_tiny_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
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
