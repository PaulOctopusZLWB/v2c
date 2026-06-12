from __future__ import annotations

from pathlib import Path

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
    assert "duration_ms=" in result.output


def test_job_status_cli_uses_config_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "configured-data"
    vault = tmp_path / "configured-vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(f"[paths]\ndata_dir = '{data_dir}'\nobsidian_vault = '{vault}'\n", encoding="utf-8")
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    record_job_run(config=config, job_name="configured-health", operation=lambda: "ok")

    result = CliRunner().invoke(app, ["job-status", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "configured-health" in result.output
    assert "succeeded" in result.output
