from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.init_health import check_health
from personal_context_node.memory_verify import verify_memory_events
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class DoctorResult:
    status: str
    database: str
    obsidian_vault: str
    source_dir: str
    archive_root: str
    pending_tasks: int
    failed_tasks: int
    recent_failed_jobs: int
    memory_invalid_events: int
    memory_materialization_mismatches: int
    funasr_runtime: str


def run_doctor(
    *,
    config: AppConfig,
    source_dir: Path | None = None,
    archive_root: Path | None = None,
) -> DoctorResult:
    health = check_health(config=config)
    pending_tasks, failed_tasks = _task_counts(config)
    recent_failed_jobs = _recent_failed_jobs(config)
    memory = verify_memory_events(config=config)
    source_status = _path_status(source_dir, required=False)
    archive_status = _path_status(archive_root, required=False)
    funasr_runtime = _funasr_runtime_status(config)
    status = "ok"
    if (
        health.status != "ok"
        or failed_tasks
        or recent_failed_jobs
        or memory.invalid_events
        or memory.materialization_mismatches
        or source_status == "missing"
        or archive_status == "missing"
        or funasr_runtime == "missing"
    ):
        status = "warning"
    return DoctorResult(
        status=status,
        database=health.database,
        obsidian_vault=health.obsidian_vault,
        source_dir=source_status,
        archive_root=archive_status,
        pending_tasks=pending_tasks,
        failed_tasks=failed_tasks,
        recent_failed_jobs=recent_failed_jobs,
        memory_invalid_events=memory.invalid_events,
        memory_materialization_mismatches=memory.materialization_mismatches,
        funasr_runtime=funasr_runtime,
    )


def _task_counts(config: AppConfig) -> tuple[int, int]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select status, count(*) as count from tasks group by status")
    finally:
        conn.close()
    pending = sum(int(row["count"]) for row in rows if row["status"] in {"pending", "claimed", "running", "failed_retryable"})
    failed = sum(int(row["count"]) for row in rows if row["status"] == "failed_terminal")
    return pending, failed


def _recent_failed_jobs(config: AppConfig) -> int:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select count(*) as count from job_runs where status = 'failed'")
    finally:
        conn.close()
    return int(rows[0]["count"]) if rows else 0


def _path_status(path: Path | None, *, required: bool) -> str:
    if path is None:
        return "skipped"
    if path.exists():
        return "ok"
    return "missing" if required else "missing"


def _funasr_runtime_status(config: AppConfig) -> str:
    if config.vad_backend != "funasr" and config.asr_backend != "funasr":
        return "skipped"
    return "ok" if importlib.util.find_spec("funasr") is not None else "missing"
