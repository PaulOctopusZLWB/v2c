from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


@dataclass(frozen=True)
class RetryTaskResult:
    task_id: str
    status: str


ALLOWED_TASK_TYPES = {
    "vad",
    "asr",
    "session_derive",
    "summarize_session",
    "daily_generate",
    "obsidian_publish",
    "archive",
}


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
    _validate_task_type(task_type)
    existing = conn.execute(
        "select task_id from tasks where task_type = ? and target_type = ? and target_id = ?",
        (task_type, target_type, target_id),
    ).fetchone()
    if existing:
        return EnqueueTaskResult(task_id=existing["task_id"], created=False)
    task_id = f"task_{uuid4().hex}"
    now = _now()
    conn.execute(
        """
        insert into tasks (
          task_id, task_type, target_type, target_id, status, available_at, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, task_type, target_type, target_id, "pending", now, now, now),
    )
    return EnqueueTaskResult(task_id=task_id, created=True)


def claim_next_task(*, config: AppConfig, task_type: str, run_id: str, lease_seconds: int = 1800) -> ClaimedTask | None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute("begin immediate")
        now = _now()
        row = conn.execute(
            """
            select *
            from tasks
            where task_type = ? and status in ('pending', 'failed_retryable')
              and available_at <= ?
            order by available_at, priority, created_at
            limit 1
            """,
            (task_type, now),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        retry_count = int(row["retry_count"]) + 1
        lease_expires_at = (datetime.fromisoformat(now) + timedelta(seconds=lease_seconds)).isoformat()
        conn.execute(
            """
            update tasks
            set status = 'claimed',
                retry_count = ?,
                attempt_count = ?,
                claimed_by_run_id = ?,
                claimed_at = ?,
                lease_expires_at = ?,
                updated_at = ?,
                last_error = null
            where task_id = ?
            """,
            (retry_count, retry_count, run_id, now, lease_expires_at, now, row["task_id"]),
        )
        conn.commit()
        return ClaimedTask(
            task_id=row["task_id"],
            task_type=row["task_type"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            status="claimed",
            attempt_count=retry_count,
            claimed_by_run_id=run_id,
        )
    finally:
        conn.close()


def start_task(*, config: AppConfig, task_id: str) -> None:
    _update_task(config=config, task_id=task_id, status="running", started_at=_now())


def succeed_task(*, config: AppConfig, task_id: str) -> None:
    _update_task(
        config=config,
        task_id=task_id,
        status="succeeded",
        finished_at=_now(),
        lease_expires_at=None,
    )


def fail_task(*, config: AppConfig, task_id: str, error: str, terminal: bool) -> None:
    _update_task(
        config=config,
        task_id=task_id,
        status="failed_terminal" if terminal else "failed_retryable",
        finished_at=_now(),
        lease_expires_at=None,
        last_error=error,
    )


def reclaim_expired_tasks(*, config: AppConfig, lease_seconds: int, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    cutoff = current.timestamp() - lease_seconds
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            "select task_id, claimed_at, lease_expires_at from tasks where status in ('claimed', 'running')",
        )
        reclaimed = 0
        for row in rows:
            lease_expires_at = row["lease_expires_at"]
            claimed_at = row["claimed_at"]
            expired = False
            if lease_expires_at:
                expired = datetime.fromisoformat(str(lease_expires_at)).timestamp() <= current.timestamp()
            elif claimed_at:
                expired = datetime.fromisoformat(str(claimed_at)).timestamp() < cutoff
            if expired:
                conn.execute(
                    """
                    update tasks
                    set status = 'pending',
                        claimed_by_run_id = null,
                        claimed_at = null,
                        lease_expires_at = null,
                        started_at = null,
                        updated_at = ?
                    where task_id = ?
                    """,
                    (_now(), row["task_id"]),
                )
                reclaimed += 1
        conn.commit()
        return reclaimed
    finally:
        conn.close()


def retry_task(*, config: AppConfig, task_id: str) -> RetryTaskResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute("select task_id from tasks where task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"task not found: {task_id}")
        conn.execute(
            """
            update tasks
            set status = 'pending',
                claimed_by_run_id = null,
                claimed_at = null,
                lease_expires_at = null,
                started_at = null,
                finished_at = null,
                updated_at = ?,
                last_error = null
            where task_id = ?
            """,
            (_now(), task_id),
        )
        conn.commit()
        return RetryTaskResult(task_id=task_id, status="pending")
    finally:
        conn.close()


def rerun_task(*, config: AppConfig, task_type: str, target_type: str, target_id: str) -> EnqueueTaskResult:
    _validate_task_type(task_type)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        existing = conn.execute(
            "select task_id from tasks where task_type = ? and target_type = ? and target_id = ?",
            (task_type, target_type, target_id),
        ).fetchone()
        if existing:
            conn.execute(
                """
                update tasks
                set status = 'pending',
                    retry_count = 0,
                    attempt_count = 0,
                    claimed_by_run_id = null,
                    claimed_at = null,
                    lease_expires_at = null,
                    started_at = null,
                    finished_at = null,
                    updated_at = ?,
                    last_error = null
                where task_id = ?
                """,
                (_now(), existing["task_id"]),
            )
            conn.commit()
            return EnqueueTaskResult(task_id=str(existing["task_id"]), created=False)
        result = enqueue_task_in_conn(conn, task_type=task_type, target_type=target_type, target_id=target_id)
        conn.commit()
        return result
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
        initialize(conn)
        fields.setdefault("updated_at", _now())
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"update tasks set {assignments} where task_id = ?", (*fields.values(), task_id))
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_task_type(task_type: str) -> None:
    if task_type not in ALLOWED_TASK_TYPES:
        raise ValueError(f"unknown task_type: {task_type}")
