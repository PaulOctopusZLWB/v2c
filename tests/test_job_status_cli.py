from __future__ import annotations

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.jobs import record_job_run


def test_job_status_cli_lists_recent_runs(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    record_job_run(config=config, job_name="health", operation=lambda: "ok")

    result = CliRunner().invoke(
        app,
        [
            "job-status",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "health" in result.output
    assert "succeeded" in result.output
