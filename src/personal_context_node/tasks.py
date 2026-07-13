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
    "transcribe_diarize",
    "asr",
    "session_derive",
    "summarize_session",
    "daily_generate",
    "obsidian_publish",
    "archive",
    "extract_features",
}


def enqueue_task(*, config: AppConfig, task_type: str, target_type: str, target_id: str, priority: int = 100) -> EnqueueTaskResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        result = enqueue_task_in_conn(
            conn,
            task_type=task_type,
            target_type=target_type,
            target_id=target_id,
            max_retries=config.task_max_retries,
            priority=priority,
        )
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
    max_retries: int = 3,
    priority: int = 100,
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
          task_id, task_type, target_type, target_id, status, priority, max_retries, available_at, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (task_id, task_type, target_type, target_id, "pending", priority, max_retries, now, now, now),
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
            where task_type = ?
              and (
                status = 'pending'
                or (status = 'failed_retryable' and retry_count < max_retries)
              )
              and available_at <= ?
            order by priority, available_at, created_at
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


def start_task(*, config: AppConfig, task_id: str, run_id: str | None = None) -> None:
    _update_task(config=config, task_id=task_id, expected_run_id=run_id, status="running", started_at=_now())


def succeed_task(*, config: AppConfig, task_id: str, run_id: str | None = None) -> bool:
    return _update_task(
        config=config,
        task_id=task_id,
        expected_run_id=run_id,
        status="succeeded",
        finished_at=_now(),
        lease_expires_at=None,
    )


def fail_task(*, config: AppConfig, task_id: str, error: str, terminal: bool, run_id: str | None = None) -> bool:
    fields: dict[str, object] = {
        "status": "failed_terminal" if terminal else "failed_retryable",
        "finished_at": _now(),
        "lease_expires_at": None,
        "last_error": error,
    }
    if not terminal:
        # Defer the next retry by an exponential backoff so a transient failure gets a real
        # recovery window (§12 retriable) instead of burning all retries instantly within a
        # single run-all drain loop. Backoff is keyed off the current retry_count.
        backoff = _retry_backoff_seconds(config=config, task_id=task_id)
        if backoff > 0:
            fields["available_at"] = (datetime.now(timezone.utc) + timedelta(seconds=backoff)).isoformat()
    return _update_task(config=config, task_id=task_id, expected_run_id=run_id, **fields)


def _retry_backoff_seconds(*, config: AppConfig, task_id: str) -> int:
    base = int(getattr(config, "task_retry_backoff_seconds", 0) or 0)
    if base <= 0:
        return 0
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute("select retry_count from tasks where task_id = ?", (task_id,)).fetchone()
    finally:
        conn.close()
    attempt = int(row["retry_count"]) if row is not None else 1
    # Exponential (base * 2^(attempt-1)) capped at the lease window so a stuck task is still
    # eventually reclaimable on its normal lease cadence.
    cap = max(base, int(getattr(config, "task_lease_seconds", base) or base))
    return min(cap, base * (2 ** max(0, attempt - 1)))


def reclaim_expired_tasks(*, config: AppConfig, lease_seconds: int, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    cutoff = current.timestamp() - lease_seconds
    conn = connect(config.database_path)
    try:
        initialize(conn)
        # Reclaim atomically under a write lock and re-check status in the UPDATE, so a
        # task that completes concurrently between the read and the reset is NOT
        # resurrected to pending (§36.1.5 reclaims crashed workers, not finished ones).
        conn.execute("begin immediate")
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
                cursor = conn.execute(
                    """
                    update tasks
                    set status = 'pending',
                        claimed_by_run_id = null,
                        claimed_at = null,
                        lease_expires_at = null,
                        started_at = null,
                        updated_at = ?
                    where task_id = ? and status in ('claimed', 'running')
                    """,
                    (_now(), row["task_id"]),
                )
                reclaimed += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        conn.commit()
        return reclaimed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def retry_task(*, config: AppConfig, task_id: str) -> RetryTaskResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute("select task_id from tasks where task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"task not found: {task_id}")
        now = _now()
        # Reset retry/attempt counters and clear any backoff window so a manual retry is
        # claimable immediately, not deferred behind the exponential backoff from fail_task.
        conn.execute(
            """
            update tasks
            set status = 'pending',
                retry_count = 0,
                attempt_count = 0,
                available_at = ?,
                claimed_by_run_id = null,
                claimed_at = null,
                lease_expires_at = null,
                started_at = null,
                finished_at = null,
                updated_at = ?,
                last_error = null
            where task_id = ?
            """,
            (now, now, task_id),
        )
        conn.commit()
        return RetryTaskResult(task_id=task_id, status="pending")
    finally:
        conn.close()


def retry_failed_tasks(*, config: AppConfig) -> int:
    """Reset ALL failed tasks (failed_terminal or failed_retryable) to pending in one UPDATE.

    Returns the number of tasks reset.  Mirrors the field set used by retry_task.
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = _now()
        cursor = conn.execute(
            """
            update tasks
            set status = 'pending',
                retry_count = 0,
                attempt_count = 0,
                available_at = ?,
                claimed_by_run_id = null,
                claimed_at = null,
                lease_expires_at = null,
                started_at = null,
                finished_at = null,
                updated_at = ?,
                last_error = null
            where status in ('failed_retryable', 'failed_terminal')
            """,
            (now, now),
        )
        conn.commit()
        return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    finally:
        conn.close()


def _rerun_asr_for_file(conn, *, target_type: str, target_id: str) -> EnqueueTaskResult | None:
    if target_type == "audio_file":
        audio_file_id: str | None = target_id
    else:
        row = conn.execute(
            "select audio_file_id from audio_chunks where chunk_id = ?",
            (target_id,),
        ).fetchone()
        audio_file_id = str(row["audio_file_id"]) if row is not None else None
    if audio_file_id is None:
        return None
    sibling_ids = [
        str(row["chunk_id"])
        for row in conn.execute(
            "select chunk_id from audio_chunks where audio_file_id = ?",
            (audio_file_id,),
        ).fetchall()
    ]
    if not sibling_ids:
        return None
    conn.execute(
        "update audio_chunks set status = 'pending_asr' where audio_file_id = ?",
        (audio_file_id,),
    )
    placeholders = ",".join("?" for _ in sibling_ids)
    conn.execute(
        f"""
        update tasks
        set status = 'pending',
            retry_count = 0,
            attempt_count = 0,
            available_at = ?,
            claimed_by_run_id = null,
            claimed_at = null,
            lease_expires_at = null,
            started_at = null,
            finished_at = null,
            updated_at = ?,
            last_error = null
        where task_type = 'asr' and target_type = 'audio_chunk'
          and target_id in ({placeholders})
        """,
        (_now(), _now(), *sibling_ids),
    )
    first_task = conn.execute(
        """
        select task_id from tasks
        where task_type = 'asr' and target_type = 'audio_chunk' and target_id = ?
        """,
        (sibling_ids[0],),
    ).fetchone()
    return EnqueueTaskResult(task_id=str(first_task["task_id"]) if first_task else sibling_ids[0], created=False)


def rerun_task(*, config: AppConfig, task_type: str, target_type: str, target_id: str) -> EnqueueTaskResult:
    _validate_task_type(task_type)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        # An ASR re-run is file-level (§36.2, `--target aud_...`): regenerate EVERY
        # chunk of the audio file so transcription (which deactivates the file's prior
        # segments) rebuilds the whole file instead of dropping un-rerun chunks. Accept
        # either an audio_file id (spec form) or an audio_chunk id (resolve its file).
        if task_type == "asr" and target_type in ("audio_file", "audio_chunk"):
            result = _rerun_asr_for_file(conn, target_type=target_type, target_id=target_id)
            if result is not None:
                conn.commit()
                return result
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
                    available_at = ?,
                    claimed_by_run_id = null,
                    claimed_at = null,
                    lease_expires_at = null,
                    started_at = null,
                    finished_at = null,
                    updated_at = ?,
                    last_error = null
                where task_id = ?
                """,
                (_now(), _now(), existing["task_id"]),
            )
            conn.commit()
            return EnqueueTaskResult(task_id=str(existing["task_id"]), created=False)
        if task_type == "asr":
            # No existing asr task and no resolvable chunks: rerun resets an EXISTING
            # target (§36.2.2); never mint a bogus unprocessable asr task.
            conn.commit()
            return EnqueueTaskResult(task_id="", created=False)
        result = enqueue_task_in_conn(conn, task_type=task_type, target_type=target_type, target_id=target_id)
        conn.commit()
        return result
    finally:
        conn.close()


def process_status_rows(*, config: AppConfig, conn: sqlite3.Connection | None = None) -> list[dict[str, object]]:
    # `conn` lets hot callers (the 1s SSE poll) reuse one connection instead of
    # reopening the DB every tick; owned connections are still closed here.
    owns_conn = conn is None
    if conn is None:
        conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select task_id, task_type, target_type, target_id, status, attempt_count,
                   retry_count, max_retries, last_error, started_at, finished_at,
                   coalesce((
                     select group_concat(distinct transcript_segments.model_name)
                     from transcript_segments
                     where tasks.task_type = 'asr'
                       and tasks.target_type = 'audio_chunk'
                       and transcript_segments.chunk_id = tasks.target_id
                   ), (
                     select group_concat(distinct summaries.model_name)
                     from summaries
                     where (
                         tasks.task_type = 'summarize_session'
                         and tasks.target_type = 'session'
                         and summaries.summary_type = 'session'
                         and summaries.target_type = 'session'
                       or
                         tasks.task_type = 'daily_generate'
                         and tasks.target_type = 'date_key'
                         and summaries.summary_type = 'daily'
                         and summaries.target_type = 'date_key'
                     )
                       and summaries.target_id = tasks.target_id
                   )) as model_name,
                   coalesce((
                     select group_concat(distinct transcript_segments.model_version)
                     from transcript_segments
                     where tasks.task_type = 'asr'
                       and tasks.target_type = 'audio_chunk'
                       and transcript_segments.chunk_id = tasks.target_id
                   ), (
                     select group_concat(distinct summaries.prompt_version)
                     from summaries
                     where (
                         tasks.task_type = 'summarize_session'
                         and tasks.target_type = 'session'
                         and summaries.summary_type = 'session'
                         and summaries.target_type = 'session'
                       or
                         tasks.task_type = 'daily_generate'
                         and tasks.target_type = 'date_key'
                         and summaries.summary_type = 'daily'
                         and summaries.target_type = 'date_key'
                     )
                       and summaries.target_id = tasks.target_id
                   )) as model_version
            from tasks
            order by created_at
            """,
        )
        for row in rows:
            row["duration_ms"] = _duration_ms(started_at=row.pop("started_at"), finished_at=row.pop("finished_at"))
        return rows
    finally:
        if owns_conn:
            conn.close()


def task_metrics(*, config: AppConfig, recent_limit: int = 500) -> dict[str, object]:
    """Per-task-type pipeline metrics: status counts + duration percentiles.

    Durations are computed over the most recent `recent_limit` finished attempts per
    task type, so the numbers track the CURRENT backend/model rather than averaging
    over the whole history of the table.
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        count_rows = fetch_all(
            conn,
            "select task_type, status, count(*) as n from tasks group by task_type, status",
        )
        duration_rows = fetch_all(
            conn,
            """
            select task_type, started_at, finished_at
            from (
              select task_type, started_at, finished_at,
                     row_number() over (partition by task_type order by finished_at desc) as rn
              from tasks
              where started_at is not null and finished_at is not null
                and status in ('succeeded', 'failed_terminal', 'failed_retryable')
            )
            where rn <= ?
            """,
            (recent_limit,),
        )
    finally:
        conn.close()
    counts_by_type: dict[str, dict[str, int]] = {}
    for row in count_rows:
        counts_by_type.setdefault(str(row["task_type"]), {})[str(row["status"])] = int(row["n"])
    durations_by_type: dict[str, list[int]] = {}
    for row in duration_rows:
        duration = _duration_ms(started_at=row["started_at"], finished_at=row["finished_at"])
        if duration is not None and duration >= 0:
            durations_by_type.setdefault(str(row["task_type"]), []).append(duration)
    task_types = []
    for task_type in sorted(set(counts_by_type) | set(durations_by_type)):
        counts = counts_by_type.get(task_type, {})
        durations = sorted(durations_by_type.get(task_type, []))
        succeeded = counts.get("succeeded", 0)
        failed = counts.get("failed_terminal", 0) + counts.get("failed_retryable", 0)
        settled = succeeded + failed
        task_types.append(
            {
                "task_type": task_type,
                "counts": counts,
                "total": sum(counts.values()),
                "success_rate": round(succeeded / settled, 4) if settled else None,
                "duration_ms": {
                    "count": len(durations),
                    "avg": round(sum(durations) / len(durations)) if durations else None,
                    "p50": _percentile(durations, 0.50),
                    "p95": _percentile(durations, 0.95),
                    "max": durations[-1] if durations else None,
                },
            }
        )
    return {"task_types": task_types, "generated_at": _now()}


def _percentile(sorted_values: list[int], q: float) -> int | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return sorted_values[index]


def _update_task(*, config: AppConfig, task_id: str, expected_run_id: str | None = None, **fields: object) -> bool:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        fields.setdefault("updated_at", _now())
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params: list[object] = [*fields.values(), task_id]
        where = "where task_id = ?"
        if expected_run_id is not None:
            # Ownership guard: a worker whose lease expired and whose task was reclaimed
            # by another run must not finalize it (§36.1.3).
            where += " and claimed_by_run_id = ?"
            params.append(expected_run_id)
        cursor = conn.execute(f"update tasks set {assignments} {where}", params)
        conn.commit()
        return bool(cursor.rowcount and cursor.rowcount > 0)
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration_ms(*, started_at: object, finished_at: object) -> int | None:
    if not started_at or not finished_at:
        return None
    started = datetime.fromisoformat(str(started_at))
    finished = datetime.fromisoformat(str(finished_at))
    return int((finished - started).total_seconds() * 1000)


def _validate_task_type(task_type: str) -> None:
    if task_type not in ALLOWED_TASK_TYPES:
        raise ValueError(f"unknown task_type: {task_type}")
