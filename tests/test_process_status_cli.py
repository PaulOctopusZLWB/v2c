from __future__ import annotations

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.tasks import enqueue_task


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
