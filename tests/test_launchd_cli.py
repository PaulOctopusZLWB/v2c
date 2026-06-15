from __future__ import annotations

import plistlib
from pathlib import Path

from typer.testing import CliRunner

from personal_context_node.cli import app
from personal_context_node.launchd import write_launchd_plists


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
            str(tmp_path / "data"),
            "--obsidian-vault",
            "/vault",
            "--source-dir",
            "/Volumes/DJI",
            "--archive-root",
            "/nas",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "plists_written=5" in result.output
    assert (output_dir / "com.personal-context-node.ingest.plist").exists()
    assert (output_dir / "com.personal-context-node.web.plist").exists()


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


def test_launchd_write_plists_uses_absolute_uv_and_creates_log_dir(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "plists"
    data_dir = tmp_path / "data"

    result = runner.invoke(
        app,
        [
            "launchd-write-plists",
            "--output-dir",
            str(output_dir),
            "--working-directory",
            str(tmp_path),
            "--data-dir",
            str(data_dir),
            "--obsidian-vault",
            str(tmp_path / "vault"),
            "--archive-root",
            str(tmp_path / "nas"),
        ],
    )

    assert result.exit_code == 0
    assert (data_dir / "logs" / "launchd").is_dir()
    plist = plistlib.loads((output_dir / "com.personal-context-node.process.plist").read_bytes())
    assert Path(plist["ProgramArguments"][0]).is_absolute()


def test_launchd_ingest_omits_source_dir_when_not_explicit(tmp_path: Path) -> None:
    paths = write_launchd_plists(
        output_dir=tmp_path / "plists",
        working_directory=str(tmp_path),
        data_dir=str(tmp_path / "data"),
        obsidian_vault=str(tmp_path / "vault"),
        source_dir=None,
        archive_root=str(tmp_path / "nas"),
        config_path="config/local.example.toml",
    )

    ingest = plistlib.loads(next(p for p in paths if "ingest" in p.name).read_bytes())

    assert "--source-dir" not in ingest["ProgramArguments"]
    assert "--config" in ingest["ProgramArguments"]


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
            str(tmp_path / "data"),
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


def test_launchd_write_plists_includes_config_so_scheduled_runs_use_real_backends(tmp_path) -> None:
    # The scheduled jobs must pass --config, else _load_config falls back to AppConfig
    # mock defaults (vad/asr/llm = "mock") in production (§6/§9).
    output_dir = tmp_path / "launchd"
    config_path = tmp_path / "config" / "local.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        "\n".join(["[paths]", f"data_dir = '{tmp_path / 'data'}'", f"obsidian_vault = '{tmp_path / 'vault'}'"]),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["launchd-write-plists", "--config", str(config_path), "--output-dir", str(output_dir), "--working-directory", "/repo"],
    )

    assert result.exit_code == 0, result.output
    for label in ("ingest", "process", "daily", "archive"):
        args = plistlib.loads((output_dir / f"com.personal-context-node.{label}.plist").read_bytes())["ProgramArguments"]
        assert "--config" in args, label
        assert str(config_path.resolve()) in args, label
