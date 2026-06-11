from __future__ import annotations

import plistlib

from personal_context_node.launchd import (
    LaunchdJob,
    install_launchd_plists,
    render_plist,
    uninstall_launchd_plists,
    write_launchd_plists,
)


def test_render_plist_contains_uv_pcn_command_and_logs() -> None:
    job = LaunchdJob(
        label="com.personal-context-node.ingest",
        command=["uv", "run", "pcn", "run-first-milestone", "--source-dir", "/Volumes/DJI"],
        start_interval_seconds=300,
        working_directory="/Users/paul/Documents/v2c 本地部署",
        log_directory="/tmp/pcn-logs",
    )

    plist_bytes = render_plist(job)
    parsed = plistlib.loads(plist_bytes)

    assert parsed["Label"] == "com.personal-context-node.ingest"
    assert parsed["ProgramArguments"] == ["uv", "run", "pcn", "run-first-milestone", "--source-dir", "/Volumes/DJI"]
    assert parsed["StartInterval"] == 300
    assert parsed["StandardOutPath"] == "/tmp/pcn-logs/com.personal-context-node.ingest.out.log"
    assert parsed["StandardErrorPath"] == "/tmp/pcn-logs/com.personal-context-node.ingest.err.log"


def test_write_launchd_plists_dry_run_writes_project_files(tmp_path) -> None:
    output_dir = tmp_path / "launchd"

    paths = write_launchd_plists(
        output_dir=output_dir,
        working_directory="/repo",
        data_dir="/repo/data",
        obsidian_vault="/vault",
        source_dir="/Volumes/DJI",
        archive_root="/nas",
        dry_run=True,
    )

    assert sorted(path.name for path in paths) == [
        "com.personal-context-node.archive.plist",
        "com.personal-context-node.daily.plist",
        "com.personal-context-node.ingest.plist",
        "com.personal-context-node.process.plist",
    ]
    process = plistlib.loads((output_dir / "com.personal-context-node.process.plist").read_bytes())
    assert "process-run" in process["ProgramArguments"]
    assert "preprocess" not in process["ProgramArguments"]
    assert "transcribe" not in process["ProgramArguments"]
    daily = plistlib.loads((output_dir / "com.personal-context-node.daily.plist").read_bytes())
    assert "process-run" in daily["ProgramArguments"]
    assert "TODAY" not in daily["ProgramArguments"]


def test_install_launchd_plists_copies_files_and_bootstraps_with_runner(tmp_path) -> None:
    source_dir = tmp_path / "generated"
    launch_agents = tmp_path / "LaunchAgents"
    paths = write_launchd_plists(
        output_dir=source_dir,
        working_directory="/repo",
        data_dir="/repo/data",
        obsidian_vault="/vault",
        source_dir="/Volumes/DJI",
        archive_root="/nas",
        dry_run=True,
    )
    commands: list[list[str]] = []

    result = install_launchd_plists(
        plist_paths=paths[:1],
        launch_agents_dir=launch_agents,
        uid=501,
        runner=lambda command: commands.append(command),
        dry_run=False,
    )

    installed = launch_agents / "com.personal-context-node.ingest.plist"
    assert result.installed_paths == [installed]
    assert installed.exists()
    assert commands == [["launchctl", "bootstrap", "gui/501", str(installed)]]


def test_launchd_install_dry_run_does_not_copy_or_run(tmp_path) -> None:
    plist = tmp_path / "com.personal-context-node.ingest.plist"
    plist.write_bytes(render_plist(LaunchdJob(
        label="com.personal-context-node.ingest",
        command=["uv", "run", "pcn", "run-first-milestone"],
        start_interval_seconds=300,
        working_directory="/repo",
        log_directory="/tmp/logs",
    )))
    launch_agents = tmp_path / "LaunchAgents"
    commands: list[list[str]] = []

    result = install_launchd_plists(
        plist_paths=[plist],
        launch_agents_dir=launch_agents,
        uid=501,
        runner=lambda command: commands.append(command),
        dry_run=True,
    )

    expected = launch_agents / "com.personal-context-node.ingest.plist"
    assert result.installed_paths == [expected]
    assert result.commands == [["launchctl", "bootstrap", "gui/501", str(expected)]]
    assert not expected.exists()
    assert commands == []


def test_uninstall_launchd_plists_boots_out_and_removes_files(tmp_path) -> None:
    launch_agents = tmp_path / "LaunchAgents"
    launch_agents.mkdir()
    plist = launch_agents / "com.personal-context-node.ingest.plist"
    plist.write_bytes(b"plist")
    commands: list[list[str]] = []

    result = uninstall_launchd_plists(
        labels=["com.personal-context-node.ingest"],
        launch_agents_dir=launch_agents,
        uid=501,
        runner=lambda command: commands.append(command),
        dry_run=False,
    )

    assert result.removed_paths == [plist]
    assert not plist.exists()
    assert commands == [["launchctl", "bootout", "gui/501", str(plist)]]
