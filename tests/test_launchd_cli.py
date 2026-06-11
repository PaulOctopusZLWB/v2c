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
