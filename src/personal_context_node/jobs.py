from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, TypeVar
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


T = TypeVar("T")


@dataclass(frozen=True)
class JobRunResult:
    run_id: str
    job_name: str
    status: str
    result: object


def record_job_run(*, config: AppConfig, job_name: str, operation: Callable[[], T]) -> JobRunResult:
    run_id = f"run_{uuid4().hex}"
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into job_runs (run_id, job_name, status, started_at) values (?, ?, ?, ?)",
            (run_id, job_name, "running", _now()),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        result = operation()
    except Exception as exc:
        _finish_run(config=config, run_id=run_id, status="failed", error=str(exc))
        raise
    _finish_run(config=config, run_id=run_id, status="succeeded", error=None)
    return JobRunResult(run_id=run_id, job_name=job_name, status="succeeded", result=result)


def job_status_rows(*, config: AppConfig, limit: int = 20) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select run_id, job_name, status, started_at, finished_at, error
            from job_runs
            order by started_at desc
            limit ?
            """,
            (limit,),
        )
    finally:
        conn.close()


def _finish_run(*, config: AppConfig, run_id: str, status: str, error: str | None) -> None:
    conn = connect(config.database_path)
    try:
        conn.execute(
            "update job_runs set status = ?, finished_at = ?, error = ? where run_id = ?",
            (status, _now(), error, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
