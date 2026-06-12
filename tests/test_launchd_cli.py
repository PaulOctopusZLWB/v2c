from __future__ import annotations

import plistlib

from typer.testing import CliRunner

from personal_context_node.cli import app


def test_launchd_write_plists_cli_writes_templates(tmp_path) -> None:
    output_dir = tmp_path / "launchd"

    result = CliRunner().invoke(
        app,
        [
            "launchd-write-plists",
            "--output-dir",
            str(output_dir),
            "--working-directory",
            "/repo",
            "--data-dir",
            "/repo/data",
            "--obsidian-vault",
            "/vault",
            "--source-dir",
            "/Volumes/DJI",
            "--archive-root",
            "/nas",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "plists_written=4" in result.output
    assert (output_dir / "com.personal-context-node.ingest.plist").exists()


def test_launchd_write_plists_cli_uses_config_path(tmp_path) -> None:
    output_dir = tmp_path / "launchd"
    config_path = tmp_path / "config" / "local.toml"
    data_dir = tmp_path / "configured-data"
    vault = tmp_path / "configured-vault"
    archive_root = tmp_path / "configured-nas"
    source_dir = tmp_path / "configured-dji"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(
            [
                "[paths]",
                f"data_dir = '{data_dir}'",
                f"obsidian_vault = '{vault}'",
                f"nas_archive_root = '{archive_root}'",
                "",
                "[device.dji_mic_3]",
                f"root_path = '{source_dir}'",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "launchd-write-plists",
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--working-directory",
            "/repo",
        ],
    )

    assert result.exit_code == 0, result.output
    ingest = plistlib.loads((output_dir / "com.personal-context-node.ingest.plist").read_bytes())
    archive = plistlib.loads((output_dir / "com.personal-context-node.archive.plist").read_bytes())
    assert str(data_dir) in ingest["ProgramArguments"]
    assert str(vault) in ingest["ProgramArguments"]
    assert str(source_dir) in ingest["ProgramArguments"]
    assert str(archive_root) in archive["ProgramArguments"]


def test_launchd_install_cli_defaults_to_dry_run(tmp_path) -> None:
    output_dir = tmp_path / "launchd"
    launch_agents = tmp_path / "LaunchAgents"
    CliRunner().invoke(
        app,
        [
            "launchd-write-plists",
            "--output-dir",
            str(output_dir),
            "--working-directory",
            "/repo",
            "--data-dir",
            "/repo/data",
            "--obsidian-vault",
            "/vault",
            "--source-dir",
            "/Volumes/DJI",
            "--archive-root",
            "/nas",
        ],
    )

    result = CliRunner().invoke(
        app,
        [
            "launchd-install",
            "--plist-dir",
            str(output_dir),
            "--launch-agents-dir",
            str(launch_agents),
            "--uid",
            "501",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "dry_run=True" in result.output
    assert "launchctl bootstrap gui/501" in result.output
    assert not (launch_agents / "com.personal-context-node.ingest.plist").exists()


def test_launchd_uninstall_cli_defaults_to_dry_run(tmp_path) -> None:
    launch_agents = tmp_path / "LaunchAgents"

    result = CliRunner().invoke(
        app,
        [
            "launchd-uninstall",
            "--launch-agents-dir",
            str(launch_agents),
            "--uid",
            "501",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "dry_run=True" in result.output
    assert "launchctl bootout gui/501" in result.output
