from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LaunchdJob:
    label: str
    command: list[str]
    start_interval_seconds: int
    working_directory: str
    log_directory: str


def render_plist(job: LaunchdJob) -> bytes:
    payload = {
        "Label": job.label,
        "ProgramArguments": job.command,
        "StartInterval": job.start_interval_seconds,
        "RunAtLoad": False,
        "WorkingDirectory": job.working_directory,
        "StandardOutPath": str(Path(job.log_directory) / f"{job.label}.out.log"),
        "StandardErrorPath": str(Path(job.log_directory) / f"{job.label}.err.log"),
    }
    return plistlib.dumps(payload, sort_keys=True)


def write_launchd_plists(
    *,
    output_dir: Path,
    working_directory: str,
    data_dir: str,
    obsidian_vault: str,
    source_dir: str,
    archive_root: str,
    dry_run: bool = True,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_directory = str(Path(data_dir) / "logs" / "launchd")
    jobs = [
        LaunchdJob(
            label="com.personal-context-node.ingest",
            command=[
                "uv",
                "run",
                "pcn",
                "run-first-milestone",
                "--source-dir",
                source_dir,
                "--data-dir",
                data_dir,
                "--obsidian-vault",
                obsidian_vault,
            ],
            start_interval_seconds=300,
            working_directory=working_directory,
            log_directory=log_directory,
        ),
        LaunchdJob(
            label="com.personal-context-node.process",
            command=[
                "uv",
                "run",
                "pcn",
                "preprocess",
                "--data-dir",
                data_dir,
                "--obsidian-vault",
                obsidian_vault,
            ],
            start_interval_seconds=600,
            working_directory=working_directory,
            log_directory=log_directory,
        ),
        LaunchdJob(
            label="com.personal-context-node.daily",
            command=[
                "uv",
                "run",
                "pcn",
                "summarize",
                "--day",
                _date_placeholder(),
                "--data-dir",
                data_dir,
                "--obsidian-vault",
                obsidian_vault,
            ],
            start_interval_seconds=86_400,
            working_directory=working_directory,
            log_directory=log_directory,
        ),
        LaunchdJob(
            label="com.personal-context-node.archive",
            command=[
                "uv",
                "run",
                "pcn",
                "archive",
                "--data-dir",
                data_dir,
                "--obsidian-vault",
                obsidian_vault,
                "--archive-root",
                archive_root,
                "--require-existing-root",
            ],
            start_interval_seconds=3_600,
            working_directory=working_directory,
            log_directory=log_directory,
        ),
    ]
    paths: list[Path] = []
    for job in jobs:
        path = output_dir / f"{job.label}.plist"
        path.write_bytes(render_plist(job))
        paths.append(path)
    return paths


def _date_placeholder() -> str:
    return "TODAY"
