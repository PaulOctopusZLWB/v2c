from __future__ import annotations

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.tasks import claim_next_task, enqueue_task, fail_task, process_status_rows, succeed_task


def test_process_retry_cli_resets_failed_task(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    task = enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None
    fail_task(config=config, task_id=task.task_id, error="failed", terminal=True)

    result = CliRunner().invoke(
        app,
        [
            "process-retry",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--task-id",
            task.task_id,
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"task_id={task.task_id}" in result.output
    assert "status=pending" in result.output


def test_process_retry_group_cli_resets_failed_task(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    task = enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None
    fail_task(config=config, task_id=task.task_id, error="failed", terminal=True)

    result = CliRunner().invoke(
        app,
        [
            "process",
            "retry",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--task-id",
            task.task_id,
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"task_id={task.task_id}" in result.output
    assert "status=pending" in result.output


def test_process_rerun_cli_reopens_existing_task(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    task = enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None
    succeed_task(config=config, task_id=task.task_id)

    result = CliRunner().invoke(
        app,
        [
            "process-rerun",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--task-type",
            "asr",
            "--target-type",
            "audio_chunk",
            "--target-id",
            "chk_1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"task_id={task.task_id}" in result.output
    assert "created=False" in result.output
    assert process_status_rows(config=config)[0]["status"] == "pending"


def test_process_rerun_group_cli_reopens_existing_task(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    task = enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None
    succeed_task(config=config, task_id=task.task_id)

    result = CliRunner().invoke(
        app,
        [
            "process",
            "rerun",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
            "--task-type",
            "asr",
            "--target-type",
            "audio_chunk",
            "--target-id",
            "chk_1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"task_id={task.task_id}" in result.output
    assert "created=False" in result.output
    assert process_status_rows(config=config)[0]["status"] == "pending"
