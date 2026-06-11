from __future__ import annotations

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.tasks import enqueue_task
from personal_context_node.storage.sqlite import connect, initialize


def test_process_status_cli_lists_tasks(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="aud_1")

    result = CliRunner().invoke(
        app,
        [
            "process-status",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "task_type=vad" in result.output
    assert "target_id=aud_1" in result.output
    assert "status=pending" in result.output


def test_process_status_cli_prints_duration_ms(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into tasks (
              task_id, task_type, target_type, target_id, status, attempt_count,
              started_at, finished_at, available_at, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task_duration",
                "asr",
                "audio_chunk",
                "chk_1",
                "succeeded",
                1,
                "2087-05-10T00:00:00+00:00",
                "2087-05-10T00:00:02+00:00",
                "2087-05-10T00:00:00+00:00",
                "2087-05-10T00:00:00+00:00",
                "2087-05-10T00:00:02+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = CliRunner().invoke(
        app,
        [
            "process-status",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "duration_ms=2000" in result.output


def test_process_status_group_cli_lists_tasks(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="aud_1")

    result = CliRunner().invoke(
        app,
        [
            "process",
            "status",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "task_type=vad" in result.output
    assert "target_id=aud_1" in result.output
    assert "status=pending" in result.output
