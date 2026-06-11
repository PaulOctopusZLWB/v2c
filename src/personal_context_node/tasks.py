from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


@dataclass(frozen=True)
class EnqueueTaskResult:
    task_id: str
    created: bool


@dataclass(frozen=True)
class ClaimedTask:
    task_id: str
    task_type: str
    target_type: str
    target_id: str
    status: str
    attempt_count: int
    claimed_by_run_id: str


def enqueue_task(*, config: AppConfig, task_type: str, target_type: str, target_id: str) -> EnqueueTaskResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        result = enqueue_task_in_conn(conn, task_type=task_type, target_type=target_type, target_id=target_id)
        conn.commit()
        return result
    finally:
        conn.close()


def enqueue_task_in_conn(
    conn: sqlite3.Connection,
    *,
    task_type: str,
    target_type: str,
    target_id: str,
) -> EnqueueTaskResult:
    existing = conn.execute(
        "select task_id from tasks where task_type = ? and target_type = ? and target_id = ?",
        (task_type, target_type, target_id),
    ).fetchone()
    if existing:
        return EnqueueTaskResult(task_id=existing["task_id"], created=False)
    task_id = f"task_{uuid4().hex}"
    conn.execute(
        """
        insert into tasks (
          task_id, task_type, target_type, target_id, status, created_at
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (task_id, task_type, target_type, target_id, "pending", _now()),
    )
    return EnqueueTaskResult(task_id=task_id, created=True)


def claim_next_task(*, config: AppConfig, task_type: str, run_id: str) -> ClaimedTask | None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute("begin immediate")
        row = conn.execute(
            """
            select *
            from tasks
            where task_type = ? and status in ('pending', 'failed_retryable')
            order by created_at
            limit 1
            """,
            (task_type,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        attempt_count = int(row["attempt_count"]) + 1
        conn.execute(
            """
            update tasks
            set status = 'claimed',
                attempt_count = ?,
                claimed_by_run_id = ?,
                claimed_at = ?,
                last_error = null
            where task_id = ?
            """,
            (attempt_count, run_id, _now(), row["task_id"]),
        )
        conn.commit()
        return ClaimedTask(
            task_id=row["task_id"],
            task_type=row["task_type"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            status="claimed",
            attempt_count=attempt_count,
            claimed_by_run_id=run_id,
        )
    finally:
        conn.close()


def start_task(*, config: AppConfig, task_id: str) -> None:
    _update_task(config=config, task_id=task_id, status="running", started_at=_now())


def succeed_task(*, config: AppConfig, task_id: str) -> None:
    _update_task(config=config, task_id=task_id, status="succeeded", finished_at=_now())


def fail_task(*, config: AppConfig, task_id: str, error: str, terminal: bool) -> None:
    _update_task(
        config=config,
        task_id=task_id,
        status="failed_terminal" if terminal else "failed_retryable",
        finished_at=_now(),
        last_error=error,
    )


def reclaim_expired_tasks(*, config: AppConfig, lease_seconds: int, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)).timestamp() - lease_seconds
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select task_id, claimed_at from tasks where status in ('claimed', 'running')")
        reclaimed = 0
        for row in rows:
            claimed_at = row["claimed_at"]
            if claimed_at and datetime.fromisoformat(str(claimed_at)).timestamp() < cutoff:
                conn.execute(
                    """
                    update tasks
                    set status = 'pending', claimed_by_run_id = null, claimed_at = null, started_at = null
                    where task_id = ?
                    """,
                    (row["task_id"],),
                )
                reclaimed += 1
        conn.commit()
        return reclaimed
    finally:
        conn.close()


def process_status_rows(*, config: AppConfig) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select task_id, task_type, target_type, target_id, status, attempt_count, last_error
            from tasks
            order by created_at
            """,
        )
    finally:
        conn.close()


def _update_task(*, config: AppConfig, task_id: str, **fields: object) -> None:
    conn = connect(config.database_path)
    try:
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"update tasks set {assignments} where task_id = ?", (*fields.values(), task_id))
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
