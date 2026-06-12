from __future__ import annotations

import hashlib
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


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


def test_first_slice_review_and_verify_commands_accept_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "local.toml"
    data_dir = tmp_path / "configured-data"
    vault = tmp_path / "configured-vault"
    config_path.parent.mkdir()
    config_path.write_text(
        f"[paths]\ndata_dir = '{data_dir}'\nobsidian_vault = '{vault}'\n",
        encoding="utf-8",
    )
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    _insert_day_for_publish(config.database_path)
    runner = CliRunner()

    publish = runner.invoke(app, ["obsidian", "publish", "--config", str(config_path), "--date", "2087-05-10"])
    sync = runner.invoke(app, ["obsidian", "sync-review", "--config", str(config_path), "--date", "2087-05-10"])
    verify = runner.invoke(app, ["memory", "verify", "--config", str(config_path)])

    assert publish.exit_code == 0, publish.output
    assert "daily_notes_written=1" in publish.output
    assert (vault / "10_Daily" / "2087-05-10.md").exists()
    assert sync.exit_code == 0, sync.output
    assert "candidates_confirmed=0" in sync.output
    assert verify.exit_code == 0, verify.output
    assert "total_events=0" in verify.output

    conn = connect(config.database_path)
    try:
        jobs = fetch_all(conn, "select job_name from job_runs where job_name = 'memory-verify'")
    finally:
        conn.close()
    assert jobs == [{"job_name": "memory-verify"}]


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


def _insert_day_for_publish(database_path: Path) -> None:
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
                "aud_publish_config",
                "DJI Mic 3",
                "/source.wav",
                "/local.wav",
                "sha256:publish",
                1000,
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T08:01:00+08:00",
                "imported",
            ),
        )
        conn.execute(
            """
            insert into sessions (
              session_id, date_key, started_at, ended_at, source,
              segment_count, active_speech_ms, first_segment_id, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ses_publish_config",
                "2087-05-10",
                "2087-05-10T08:00:00+08:00",
                "2087-05-10T08:00:01+08:00",
                "derived_from_segments",
                1,
                1000,
                "seg_publish_config",
                "2087-05-10T08:01:00+08:00",
                "2087-05-10T08:01:00+08:00",
            ),
        )
        conn.execute(
            """
            insert into transcript_segments (
              segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text,
              language, speaker, evidence_id, confidence, asr_backend, model_name, model_version
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "seg_publish_config",
                "aud_publish_config",
                "chk_publish_config",
                "ses_publish_config",
                0,
                1000,
                "配置驱动命令需要能发布 Obsidian。",
                "zh",
                "self",
                "ev_publish_config",
                0.99,
                "mock",
                "mock-asr",
                "test",
            ),
        )
        conn.execute(
            """
            insert into summaries (
              summary_id, summary_type, target_type, target_id, prompt_version,
              model_name, content_json, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sum_publish_config",
                "daily",
                "date_key",
                "2087-05-10",
                "llm_port.daily_summary.v1",
                "llm_port",
                '{"schema_version":"daily_summary.v1","date_key":"2087-05-10","headline":"配置驱动日报","summary":"配置驱动日报","highlights":[],"inferences":[],"decisions_rollup":[],"todos_rollup":[]}',
                "2087-05-10T08:02:00+08:00",
                "2087-05-10T08:02:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
