from __future__ import annotations

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.system_summary import daily_system_summary


def test_daily_system_summary_counts_jobs_tasks_and_archives(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_summary_inputs(config)

    summary = daily_system_summary(config=config, day="2087-05-10")

    assert summary.day == "2087-05-10"
    assert summary.jobs_total == 2
    assert summary.jobs_succeeded == 1
    assert summary.jobs_failed == 1
    assert summary.tasks_pending == 1
    assert summary.tasks_failed == 2
    assert summary.archived_records == 1


def test_system_summary_cli_prints_daily_operational_summary(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_summary_inputs(config)

    result = CliRunner().invoke(
        app,
        [
            "system-summary",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--day",
            "2087-05-10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "day=2087-05-10" in result.output
    assert "jobs_total=2" in result.output
    assert "jobs_failed=1" in result.output
    assert "tasks_pending=1" in result.output
    assert "tasks_failed=2" in result.output
    assert "archived_records=1" in result.output


def _insert_summary_inputs(config: AppConfig) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into job_runs (run_id, job_name, status, started_at, finished_at, error) values (?, ?, ?, ?, ?, ?)",
            ("run_ok", "process-run", "succeeded", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:01+08:00", None),
        )
        conn.execute(
            "insert into job_runs (run_id, job_name, status, started_at, finished_at, error) values (?, ?, ?, ?, ?, ?)",
            ("run_failed", "process-run", "failed", "2087-05-10T09:00:00+08:00", "2087-05-10T09:00:02+08:00", "boom"),
        )
        conn.execute(
            "insert into job_runs (run_id, job_name, status, started_at, finished_at, error) values (?, ?, ?, ?, ?, ?)",
            ("run_other_day", "process-run", "failed", "2087-05-11T09:00:00+08:00", "2087-05-11T09:00:02+08:00", "boom"),
        )
        conn.execute(
            """
            insert into tasks (task_id, task_type, target_type, target_id, status, created_at, updated_at, available_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("task_pending", "vad", "audio_file", "aud_1", "pending", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            """
            insert into tasks (task_id, task_type, target_type, target_id, status, created_at, updated_at, available_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("task_retry", "asr", "audio_chunk", "chk_1", "failed_retryable", "2087-05-10T08:00:00+08:00", "2087-05-10T08:05:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            """
            insert into tasks (task_id, task_type, target_type, target_id, status, created_at, updated_at, available_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("task_terminal", "asr", "audio_chunk", "chk_2", "failed_terminal", "2087-05-10T08:00:00+08:00", "2087-05-10T08:05:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            """
            insert into archive_records (
              archive_record_id, target_type, target_id, source_path, archive_path, sha256,
              status, verified, archived_at, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "arc_1",
                "audio_file",
                "aud_1",
                "/tmp/source.wav",
                "/tmp/archive.wav",
                "abc",
                "verified",
                1,
                "2087-05-10T10:00:00+08:00",
                "2087-05-10T10:00:00+08:00",
                "2087-05-10T10:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
