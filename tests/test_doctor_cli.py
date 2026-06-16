from __future__ import annotations

import importlib.util
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.config import AppConfig
from personal_context_node.tasks import claim_next_task, enqueue_task, fail_task


def test_doctor_cli_reports_ok_for_initialized_workspace(tmp_path) -> None:
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    source_dir = tmp_path / "sample_data"
    archive_root = tmp_path / "archive"
    source_dir.mkdir()
    archive_root.mkdir()
    runner = CliRunner()
    init_result = runner.invoke(
        app,
        ["init", "--data-dir", str(data_dir), "--obsidian-vault", str(vault)],
    )
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(
        app,
        [
            "doctor",
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
            "--source-dir",
            str(source_dir),
            "--archive-root",
            str(archive_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=ok" in result.output
    assert "database=ok" in result.output
    assert "obsidian_vault=ok" in result.output
    assert "source_dir=ok" in result.output
    assert "archive_root=ok" in result.output
    assert "failed_tasks=0" in result.output
    assert "memory_invalid_events=0" in result.output


def test_doctor_cli_reports_warning_when_failed_tasks_exist(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="chk_1")
    claimed = claim_next_task(config=config, task_type="asr", run_id="run_1")
    assert claimed is not None
    fail_task(config=config, task_id=claimed.task_id, error="model unavailable", terminal=True)

    result = CliRunner().invoke(
        app,
        [
            "doctor",
            "--data-dir",
            str(config.data_dir),
            "--obsidian-vault",
            str(config.obsidian_vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=warning" in result.output
    assert "failed_tasks=1" in result.output


def test_doctor_cli_reports_missing_funasr_runtime_when_funasr_backend_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "funasr" else object())
    data = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        f"""
[paths]
data_dir = "{data}"
obsidian_vault = "{vault}"

[vad]
backend = "funasr"

[asr]
backend = "funasr"
""".strip(),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["doctor", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    assert "status=warning" in result.output
    assert "funasr_runtime=missing" in result.output


def test_doctor_cli_uses_config_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "configured-data"
    vault = tmp_path / "configured-vault"
    source_dir = tmp_path / "sample_data"
    archive_root = tmp_path / "archive"
    config_path = tmp_path / "config" / "local.toml"
    source_dir.mkdir()
    archive_root.mkdir()
    runner = CliRunner()
    init_result = runner.invoke(
        app,
        [
            "init",
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
            "--config-path",
            str(config_path),
        ],
    )
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(
        app,
        [
            "doctor",
            "--config",
            str(config_path),
            "--source-dir",
            str(source_dir),
            "--archive-root",
            str(archive_root),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=ok" in result.output
    assert "database=ok" in result.output
    assert "obsidian_vault=ok" in result.output
    assert "source_dir=ok" in result.output
    assert "archive_root=ok" in result.output


def test_funasr_runtime_required_for_funasr_server_backend(tmp_path) -> None:
    from personal_context_node.config import AppConfig
    from personal_context_node.doctor import _funasr_runtime_status

    # funasr_server needs the runtime even with a non-funasr VAD -> must NOT be skipped.
    assert _funasr_runtime_status(AppConfig(vad_backend="energy", asr_backend="funasr_server")) != "skipped"
    # all-mock -> skipped.
    assert _funasr_runtime_status(AppConfig(vad_backend="mock", asr_backend="mock")) == "skipped"
