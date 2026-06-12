from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.daily_reports import set_daily_report_status


def test_daily_status_cli_reports_day_status(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    set_daily_report_status(config=config, day="2087-05-10", status="review_pending")

    result = CliRunner().invoke(
        app,
        [
            "daily-status",
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
    assert "status=review_pending" in result.output


def test_daily_status_cli_uses_config_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "configured-data"
    vault = tmp_path / "configured-vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(f"[paths]\ndata_dir = '{data_dir}'\nobsidian_vault = '{vault}'\n", encoding="utf-8")
    config = AppConfig(data_dir=data_dir, obsidian_vault=vault)
    set_daily_report_status(config=config, day="2087-05-10", status="review_synced")

    result = CliRunner().invoke(app, ["daily-status", "--config", str(config_path), "--day", "2087-05-10"])

    assert result.exit_code == 0, result.output
    assert "day=2087-05-10" in result.output
    assert "status=review_synced" in result.output
