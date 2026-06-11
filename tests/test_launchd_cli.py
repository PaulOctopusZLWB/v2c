from __future__ import annotations

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
