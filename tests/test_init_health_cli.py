from __future__ import annotations

from typer.testing import CliRunner

from personal_context_node.cli import app


def test_init_cli_creates_local_directories_and_config(tmp_path) -> None:
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    config_path = tmp_path / "config" / "local.toml"

    result = CliRunner().invoke(
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

    assert result.exit_code == 0, result.output
    assert "initialized=True" in result.output
    assert (data_dir / "db").is_dir()
    assert (data_dir / "audio" / "raw").is_dir()
    assert (data_dir / "audio" / "work").is_dir()
    assert (data_dir / "keys").is_dir()
    assert (data_dir / "logs").is_dir()
    assert (vault / "10_Daily").is_dir()
    assert (vault / "30_Memory_Candidates").is_dir()
    assert config_path.exists()
    config_text = config_path.read_text(encoding="utf-8")
    assert "raw_audio_dir" in config_text
    assert "work_audio_dir" in config_text
    assert "sqlite_path" in config_text
    assert "identity_dir" in config_text
    assert "obsidian_vault" in config_text
    assert "send_person_names = true" in config_text
    assert "send_speaker_labels = true" in config_text
    assert "max_chunk_tokens = 6000" in config_text
    assert "[identity]" in config_text
    assert 'owner_did = "did:key:local-owner"' in config_text
    assert 'signing_key_path = "' in config_text


def test_health_cli_reports_ok_for_initialized_workspace(tmp_path) -> None:
    data_dir = tmp_path / "data"
    vault = tmp_path / "vault"
    runner = CliRunner()
    init_result = runner.invoke(
        app,
        [
            "init",
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
        ],
    )
    assert init_result.exit_code == 0, init_result.output

    result = runner.invoke(
        app,
        [
            "health",
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(vault),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=ok" in result.output
    assert "database=ok" in result.output
    assert "obsidian_vault=ok" in result.output
