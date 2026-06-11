from __future__ import annotations

import hashlib
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


def test_archive_cli_uses_config_archive_root(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    data_dir = tmp_path / "data"
    archive_root = tmp_path / "nas"
    vault = tmp_path / "vault"
    config_path.write_text(
        f"[paths]\ndata_dir = '{data_dir}'\nobsidian_vault = '{vault}'\nnas_archive_root = '{archive_root}'\n",
        encoding="utf-8",
    )
    raw_path = data_dir / "audio" / "raw" / "2087-05-10" / "sample.wav"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw audio bytes")
    _insert_audio(data_dir / "db" / "personal_context.sqlite", raw_path, _sha256(raw_path))

    result = CliRunner().invoke(app, ["archive", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "files_archived=1" in result.output
    assert (archive_root / "audio" / "raw" / "2087-05-10" / "sample.wav").exists()


def _insert_audio(database_path: Path, raw_path: Path, sha256: str) -> None:
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
                "imported",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
