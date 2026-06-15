from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


_DEFAULT_ENVIRONMENT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


@dataclass(frozen=True)
class LaunchdJob:
    label: str
    command: list[str]
    start_interval_seconds: int
    working_directory: str
    log_directory: str
    start_calendar: dict[str, int] | None = None
    run_at_load: bool = False
    keep_alive: bool = False
    environment_path: str = _DEFAULT_ENVIRONMENT_PATH


@dataclass(frozen=True)
class LaunchdInstallResult:
    installed_paths: list[Path]
    commands: list[list[str]]


@dataclass(frozen=True)
class LaunchdUninstallResult:
    removed_paths: list[Path]
    commands: list[list[str]]


def render_plist(job: LaunchdJob) -> bytes:
    payload: dict[str, object] = {
        "Label": job.label,
        "ProgramArguments": job.command,
        "EnvironmentVariables": {"PATH": job.environment_path},
        "RunAtLoad": job.run_at_load,
        "WorkingDirectory": job.working_directory,
        "StandardOutPath": str(Path(job.log_directory) / f"{job.label}.out.log"),
        "StandardErrorPath": str(Path(job.log_directory) / f"{job.label}.err.log"),
    }
    # A long-running KeepAlive service is mutually exclusive with the scheduling keys:
    # launchd restarts it instead of running it on an interval/calendar.
    if job.keep_alive:
        payload["KeepAlive"] = True
    # A fixed-time daily job uses StartCalendarInterval (wall-clock), a periodic job
    # uses StartInterval (rolling). They are mutually exclusive.
    elif job.start_calendar is not None:
        payload["StartCalendarInterval"] = job.start_calendar
    else:
        payload["StartInterval"] = job.start_interval_seconds
    return plistlib.dumps(payload, sort_keys=True)


def _resolve_uv() -> str:
    resolved = shutil.which("uv")
    return resolved or "uv"


def _environment_path(uv_bin: str) -> str:
    # Ensure the directory uv was resolved from is on the job's PATH. The standalone installer
    # puts uv in ~/.local/bin (not Homebrew), and adapter wrapper commands inherit this PATH;
    # without uv's own dir, a wrapper that shells out to a co-located tool fails under launchd.
    uv_path = Path(uv_bin)
    if not uv_path.is_absolute():
        return _DEFAULT_ENVIRONMENT_PATH
    uv_dir = str(uv_path.parent)
    if uv_dir in _DEFAULT_ENVIRONMENT_PATH.split(":"):
        return _DEFAULT_ENVIRONMENT_PATH
    return f"{uv_dir}:{_DEFAULT_ENVIRONMENT_PATH}"


def write_launchd_plists(
    *,
    output_dir: Path,
    working_directory: str,
    data_dir: str,
    obsidian_vault: str,
    source_dir: str | None,
    archive_root: str,
    config_path: str | None = None,
    dry_run: bool = True,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Only the log directory PATH is recorded in the plists here; it is created at install
    # time (install_launchd_plists), so generating/previewing templates never depends on the
    # data volume being mounted/writable.
    log_directory = str(Path(data_dir) / "logs" / "launchd")
    # Resolve uv to an absolute path: launchd jobs run with a minimal PATH, so a bare
    # "uv" is not found and the job fails to even start.
    uv_bin = _resolve_uv()
    environment_path = _environment_path(uv_bin)
    # Omit --source-dir unless an explicit source is configured, so scheduled ingest falls
    # back to configured device discovery (DJI volume auto-detect) instead of a fixed path.
    source_args = ["--source-dir", source_dir] if source_dir else []
    # Pass --config so scheduled jobs use the configured real VAD/ASR/LLM backends and
    # bound owner_did, not the AppConfig mock/placeholder defaults (§6/§9, §30.3).
    config_args = ["--config", config_path] if config_path else []
    jobs = [
        # ingest: real import driven by config-based device discovery; enqueues the
        # first pipeline task (vad). It does NOT run the mock E2E orchestrator.
        LaunchdJob(
            label="com.personal-context-node.ingest",
            command=[
                uv_bin,
                "run",
                "pcn",
                "ingest-import",
                *config_args,
                *source_args,
                "--data-dir",
                data_dir,
                "--obsidian-vault",
                obsidian_vault,
            ],
            start_interval_seconds=300,
            working_directory=working_directory,
            log_directory=log_directory,
            environment_path=environment_path,
        ),
        # process: advances the declarative pipeline DAG (vad -> asr -> ... -> publish).
        LaunchdJob(
            label="com.personal-context-node.process",
            command=[
                uv_bin,
                "run",
                "pcn",
                "process-run",
                *config_args,
                "--data-dir",
                data_dir,
                "--obsidian-vault",
                obsidian_vault,
            ],
            start_interval_seconds=600,
            working_directory=working_directory,
            log_directory=log_directory,
            environment_path=environment_path,
        ),
        # daily: fixed wall-clock time (23:30); drives any pending daily_generate /
        # obsidian_publish work and exits when there is nothing new for the day.
        LaunchdJob(
            label="com.personal-context-node.daily",
            command=[
                uv_bin,
                "run",
                "pcn",
                "process-run",
                *config_args,
                "--data-dir",
                data_dir,
                "--obsidian-vault",
                obsidian_vault,
            ],
            start_interval_seconds=86_400,
            working_directory=working_directory,
            log_directory=log_directory,
            environment_path=environment_path,
            start_calendar={"Hour": 23, "Minute": 30},
        ),
        LaunchdJob(
            label="com.personal-context-node.archive",
            command=[
                uv_bin,
                "run",
                "pcn",
                "archive",
                *config_args,
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
            environment_path=environment_path,
        ),
        # web: long-running local control-panel HTTP service. Unlike the periodic jobs,
        # it loads at login and is kept alive (restarted) by launchd, so it carries no
        # StartInterval/StartCalendarInterval.
        LaunchdJob(
            label="com.personal-context-node.web",
            command=[
                uv_bin,
                "run",
                "pcn",
                "web",
                *config_args,
                "--port",
                "8765",
            ],
            start_interval_seconds=0,
            working_directory=working_directory,
            log_directory=log_directory,
            environment_path=environment_path,
            run_at_load=True,
            keep_alive=True,
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
        _ensure_log_directory(source)
        shutil.copy2(source, destination)
        run(command)
    return LaunchdInstallResult(installed_paths=installed_paths, commands=commands)


def _ensure_log_directory(plist_path: Path) -> None:
    # launchd will not create the StandardOutPath/StandardErrorPath directory, so create it
    # at activation time (on the machine where the data volume is mounted) — otherwise job
    # stdout/stderr is silently dropped.
    payload = plistlib.loads(plist_path.read_bytes())
    out_path = payload.get("StandardOutPath")
    if isinstance(out_path, str) and out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)


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


def _run_command(command: list[str]) -> None:
    subprocess.run(command, check=True)
