from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class LaunchdJob:
    label: str
    command: list[str]
    start_interval_seconds: int
    working_directory: str
    log_directory: str


@dataclass(frozen=True)
class LaunchdInstallResult:
    installed_paths: list[Path]
    commands: list[list[str]]


@dataclass(frozen=True)
class LaunchdUninstallResult:
    removed_paths: list[Path]
    commands: list[list[str]]


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


def install_launchd_plists(
    *,
    plist_paths: list[Path],
    launch_agents_dir: Path,
    uid: int | None = None,
    runner: Callable[[list[str]], object] | None = None,
    dry_run: bool = True,
) -> LaunchdInstallResult:
    uid = uid if uid is not None else os.getuid()
    installed_paths = [launch_agents_dir / path.name for path in plist_paths]
    commands = [["launchctl", "bootstrap", f"gui/{uid}", str(path)] for path in installed_paths]
    if dry_run:
        return LaunchdInstallResult(installed_paths=installed_paths, commands=commands)
    launch_agents_dir.mkdir(parents=True, exist_ok=True)
    run = runner or _run_command
    for source, destination, command in zip(plist_paths, installed_paths, commands, strict=True):
        shutil.copy2(source, destination)
        run(command)
    return LaunchdInstallResult(installed_paths=installed_paths, commands=commands)


def uninstall_launchd_plists(
    *,
    labels: list[str],
    launch_agents_dir: Path,
    uid: int | None = None,
    runner: Callable[[list[str]], object] | None = None,
    dry_run: bool = True,
) -> LaunchdUninstallResult:
    uid = uid if uid is not None else os.getuid()
    removed_paths = [launch_agents_dir / f"{label}.plist" for label in labels]
    commands = [["launchctl", "bootout", f"gui/{uid}", str(path)] for path in removed_paths]
    if dry_run:
        return LaunchdUninstallResult(removed_paths=removed_paths, commands=commands)
    run = runner or _run_command
    for path, command in zip(removed_paths, commands, strict=True):
        run(command)
        if path.exists():
            path.unlink()
    return LaunchdUninstallResult(removed_paths=removed_paths, commands=commands)


def _date_placeholder() -> str:
    return "TODAY"


def _run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)
