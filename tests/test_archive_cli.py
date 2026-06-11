from __future__ import annotations

import hashlib
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_archive_cli_archives_imported_audio(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    _insert_audio(config.database_path, raw_path, _sha256(raw_path))
    archive_root = tmp_path / "nas" / "PersonalContext"

    result = CliRunner().invoke(
        app,
        [
            "archive",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--archive-root",
            str(archive_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "files_archived=1" in result.output
    assert "files_pending=0" in result.output
    assert "events_archived=0" in result.output
    assert "transcripts_archived=0" in result.output
    assert "summaries_archived=0" in result.output
    assert (archive_root / "audio" / "raw" / "2087-05-10" / "sample.wav").exists()


def test_archive_run_group_cli_archives_imported_audio(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    _insert_audio(config.database_path, raw_path, _sha256(raw_path))
    archive_root = tmp_path / "nas" / "PersonalContext"

    result = CliRunner().invoke(
        app,
        [
            "archive",
            "run",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--archive-root",
            str(archive_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "files_archived=1" in result.output
    assert "files_pending=0" in result.output
    assert "events_archived=0" in result.output
    assert "transcripts_archived=0" in result.output
    assert "summaries_archived=0" in result.output
    assert (archive_root / "audio" / "raw" / "2087-05-10" / "sample.wav").exists()


def test_archive_cleanup_cli_removes_verified_retained_local_audio(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    raw_path = config.data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    archive_root = tmp_path / "nas" / "PersonalContext"
    archive_path = archive_root / "audio" / "raw" / "2087-05-10" / "sample.wav"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(raw_path.read_bytes())
    _insert_audio(config.database_path, raw_path, _sha256(raw_path), status="archived")
    _insert_archive_record(
        config.database_path,
        source_path=raw_path,
        archive_path=archive_path,
        sha256=_sha256(archive_path),
        archived_at="2087-05-01T00:00:00+00:00",
    )

    result = CliRunner().invoke(
        app,
        [
            "archive",
            "cleanup",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--archive-root",
            str(archive_root),
            "--archived-before",
            "2087-05-05T00:00:00+00:00",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "files_removed=1" in result.output
    assert "files_pending=0" in result.output
    assert not raw_path.exists()
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select status from audio_files where audio_file_id = 'aud_test'")
    finally:
        conn.close()
    assert rows == [{"status": "locally_removed"}]


def _insert_audio(database_path: Path, raw_path: Path, sha256: str, *, status: str = "imported") -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "aud_test",
                "DJI Mic 3",
                "/source.wav",
                str(raw_path),
                sha256,
                1000,
                "2087-05-10T00:00:00+08:00",
                "2087-05-10T00:10:00+08:00",
                status,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _insert_archive_record(
    database_path: Path,
    *,
    source_path: Path,
    archive_path: Path,
    sha256: str,
    archived_at: str,
) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into archive_records (
              archive_record_id, target_type, target_id, audio_file_id,
              source_path, archive_path, sha256, status, verified, archived_at,
              created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "arc_test",
                "audio_file",
                "aud_test",
                "aud_test",
                str(source_path),
                str(archive_path),
                sha256,
                "verified",
                1,
                archived_at,
                archived_at,
                archived_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
