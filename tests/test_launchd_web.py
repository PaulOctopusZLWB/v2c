from __future__ import annotations

import plistlib
from pathlib import Path

from personal_context_node.launchd import LaunchdJob, render_plist, write_launchd_plists


def test_render_keepalive_runatload_job_omits_start_interval() -> None:
    job = LaunchdJob(
        label="com.personal-context-node.web",
        command=["uv", "run", "pcn", "web", "--port", "8765"],
        start_interval_seconds=0,
        working_directory="/repo",
        log_directory="/repo/data/logs/launchd",
        run_at_load=True,
        keep_alive=True,
    )
    payload = plistlib.loads(render_plist(job))
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert "StartInterval" not in payload
    assert payload["ProgramArguments"] == ["uv", "run", "pcn", "web", "--port", "8765"]


def test_existing_jobs_unchanged_and_web_job_added(tmp_path: Path) -> None:
    paths = write_launchd_plists(
        output_dir=tmp_path,
        working_directory="/repo",
        data_dir="/repo/data",
        obsidian_vault="/vault",
        source_dir="/Volumes/DJI",
        archive_root="/nas",
        dry_run=True,
    )
    labels = {Path(p).stem for p in paths}
    assert "com.personal-context-node.web" in labels
    web_plist = next(p for p in paths if Path(p).stem == "com.personal-context-node.web")
    payload = plistlib.loads(Path(web_plist).read_bytes())
    assert payload["KeepAlive"] is True
    assert payload["RunAtLoad"] is True
    assert "web" in payload["ProgramArguments"]
