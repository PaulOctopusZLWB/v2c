from __future__ import annotations

import plistlib

from personal_context_node.launchd import LaunchdJob, render_plist, write_launchd_plists


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
    assert "preprocess" in process["ProgramArguments"]
    assert "transcribe" not in process["ProgramArguments"]
