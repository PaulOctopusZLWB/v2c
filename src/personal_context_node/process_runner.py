from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.archive import archive_completed_audio
from personal_context_node.archive_adapters import build_archive_adapter
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.errors import TerminalPortError
from personal_context_node.core.ports.llm import LLMPort
from personal_context_node.core.ports.asr import ASRPort
from personal_context_node.core.ports.vad import VADPort
from personal_context_node.daily_reports import set_daily_report_status
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.obsidian_publish import publish_obsidian_day
from personal_context_node.obsidian_review import confirm_checked_candidates
from personal_context_node.session_summaries import summarize_session
from personal_context_node.sessions import derive_sessions_for_day
from personal_context_node.speaker_review import sync_speaker_review
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.tasks import claim_next_task, enqueue_task_in_conn, fail_task, reclaim_expired_tasks, start_task
from personal_context_node.transcription import transcribe_pending_chunks


@dataclass(frozen=True)
class ProcessOnceResult:
    task_id: str | None
    task_type: str | None
    status: str


@dataclass(frozen=True)
class PipelineEdge:
    upstream_task_type: str
    downstream_task_type: str
    downstream_target_type: str
    target_ids: Callable[[sqlite3.Connection, AppConfig, str], list[str]]


PIPELINE = (
    PipelineEdge("vad", "asr", "audio_chunk", lambda conn, config, target_id: _chunk_ids_for_audio_file_in_conn(conn, audio_file_id=target_id)),
    PipelineEdge("asr", "session_derive", "date_key", lambda conn, config, target_id: _ready_session_derive_dates_in_conn(conn, chunk_id=target_id)),
    PipelineEdge("session_derive", "summarize_session", "session", lambda conn, config, target_id: _session_ids_for_day_in_conn(conn, day=target_id)),
    PipelineEdge("summarize_session", "daily_generate", "date_key", lambda conn, config, target_id: _ready_daily_generate_dates_in_conn(conn, session_id=target_id)),
    PipelineEdge("daily_generate", "obsidian_publish", "date_key", lambda _conn, _config, target_id: [target_id]),
)
PROCESS_TASK_ORDER = (
    "vad",
    "asr",
    "session_derive",
    "summarize_session",
    "daily_generate",
    "obsidian_publish",
    "archive",
)


def process_once(
    *,
    config: AppConfig,
    run_id: str,
    vad: VADPort,
    asr: ASRPort,
    llm: LLMPort | None = None,
    max_chunk_ms: int = 30_000,
) -> ProcessOnceResult:
    llm_adapter = llm or RuleBasedLLMAdapter()
    reclaim_expired_tasks(config=config, lease_seconds=config.task_lease_seconds)
    task = None
    for task_type in PROCESS_TASK_ORDER:
        task = claim_next_task(config=config, task_type=task_type, run_id=run_id, lease_seconds=config.task_lease_seconds)
        if task is not None:
            break
    if task is None:
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    try:
        start_task(config=config, task_id=task.task_id, run_id=run_id)
        if task.task_type == "vad":
            preprocess_imported_audio(
                config=config,
                vad=vad,
                max_chunk_ms=max_chunk_ms,
                # Overlap only applies when strictly smaller than the chunk size; a
                # configured overlap >= max_chunk_ms is degenerate, so disable it.
                chunk_overlap_ms=config.chunk_overlap_ms if config.chunk_overlap_ms < max_chunk_ms else 0,
                audio_file_id=task.target_id,
            )
        elif task.task_type == "asr":
            transcribe_pending_chunks(config=config, asr=asr, chunk_id=task.target_id)
        elif task.task_type == "session_derive":
            derive_sessions_for_day(config=config, day=task.target_id, session_gap_minutes=config.session_gap_minutes)
        elif task.task_type == "summarize_session":
            summarize_session(config=config, session_id=task.target_id, llm=llm_adapter)
            _succeed_task_and_enqueue_downstream(config=config, task_id=task.task_id, run_id=run_id, upstream_task_type=task.task_type, upstream_target_id=task.target_id)
            return ProcessOnceResult(task_id=task.task_id, task_type=task.task_type, status="succeeded")
        elif task.task_type == "daily_generate":
            confirm_checked_candidates(config=config, day=task.target_id)
            sync_speaker_review(config=config, day=task.target_id)
            set_daily_report_status(config=config, day=task.target_id, status="generating")
            generate_daily_context(config=config, day=task.target_id, llm=llm_adapter)
        elif task.task_type == "obsidian_publish":
            publish_obsidian_day(config=config, day=task.target_id, source_run_id=run_id)
        elif task.task_type == "archive":
            archive_completed_audio(
                config=config,
                archive=build_archive_adapter(config=config),
            )
        else:
            raise ValueError(f"unsupported task type: {task.task_type}")
        _succeed_task_and_enqueue_downstream(config=config, task_id=task.task_id, run_id=run_id, upstream_task_type=task.task_type, upstream_target_id=task.target_id)
        return ProcessOnceResult(task_id=task.task_id, task_type=task.task_type, status="succeeded")
    except Exception as exc:
        if task.task_type == "daily_generate":
            set_daily_report_status(config=config, day=task.target_id, status="failed", error=str(exc))
        fail_task(config=config, task_id=task.task_id, error=str(exc), terminal=isinstance(exc, TerminalPortError), run_id=run_id)
        # A task that has reached a terminal state (failed_terminal, or failed_retryable
        # with retries exhausted) is "done (failed)". Re-evaluate the fan-in so a failure
        # that completes a fan-in set does not silently deadlock the downstream (the
        # predicates already treat a failed sibling as done). Best-effort: never mask the
        # original task error.
        try:
            _enqueue_downstream_after_terminal_failure(
                config=config, task_id=task.task_id, upstream_task_type=task.task_type, upstream_target_id=task.target_id
            )
        except Exception:
            pass
        raise


def preview_next_process_task(*, config: AppConfig) -> ProcessOnceResult:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = datetime.now(timezone.utc).isoformat()
        for task_type in PROCESS_TASK_ORDER:
            row = conn.execute(
                """
                select task_id, task_type
                from tasks
                where task_type = ?
                  and (
                    status = 'pending'
                    or (status = 'failed_retryable' and retry_count < max_retries)
                  )
                  and available_at <= ?
                order by available_at, priority, created_at
                limit 1
                """,
                (task_type, now),
            ).fetchone()
            if row is not None:
                return ProcessOnceResult(task_id=row["task_id"], task_type=row["task_type"], status="dry_run")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")
    finally:
        conn.close()


def _succeed_task_and_enqueue_downstream(
    *,
    config: AppConfig,
    task_id: str,
    upstream_task_type: str,
    upstream_target_id: str,
    run_id: str | None = None,
) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute("begin immediate")
        now = datetime.now(timezone.utc).isoformat()
        params: list[object] = [now, now, task_id]
        where = "where task_id = ?"
        if run_id is not None:
            # Ownership guard: only the run that still holds the claim may finalize and
            # fan out — a reclaimed/re-claimed task must not be double-completed (§36.1.3).
            where += " and claimed_by_run_id = ?"
            params.append(run_id)
        cursor = conn.execute(
            f"""
            update tasks
            set status = 'succeeded',
                finished_at = ?,
                lease_expires_at = null,
                updated_at = ?
            {where}
            """,
            params,
        )
        if run_id is not None and not (cursor.rowcount and cursor.rowcount > 0):
            # This run no longer owns the task (lease expired + reclaimed): do not
            # enqueue downstream off a claim we lost.
            conn.rollback()
            return
        _enqueue_downstream_tasks_in_conn(
            conn,
            config=config,
            upstream_task_type=upstream_task_type,
            upstream_target_id=upstream_target_id,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _enqueue_downstream_after_terminal_failure(
    *,
    config: AppConfig,
    task_id: str,
    upstream_task_type: str,
    upstream_target_id: str,
) -> None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute("begin immediate")
        row = conn.execute(
            "select status, retry_count, max_retries from tasks where task_id = ?",
            (task_id,),
        ).fetchone()
        is_terminal = row is not None and (
            str(row["status"]) == "failed_terminal"
            or (str(row["status"]) == "failed_retryable" and int(row["retry_count"]) >= int(row["max_retries"]))
        )
        if is_terminal:
            _enqueue_downstream_tasks_in_conn(
                conn, config=config, upstream_task_type=upstream_task_type, upstream_target_id=upstream_target_id
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _enqueue_downstream_tasks_in_conn(
    conn: sqlite3.Connection,
    *,
    config: AppConfig,
    upstream_task_type: str,
    upstream_target_id: str,
) -> None:
    for edge in PIPELINE:
        if edge.upstream_task_type != upstream_task_type:
            continue
        for target_id in edge.target_ids(conn, config, upstream_target_id):
            result = enqueue_task_in_conn(
                conn,
                task_type=edge.downstream_task_type,
                target_type=edge.downstream_target_type,
                target_id=target_id,
                max_retries=config.task_max_retries,
            )
            if not result.created:
                _reset_downstream_task_in_conn(conn, task_id=result.task_id)


def _reset_downstream_task_in_conn(conn: sqlite3.Connection, *, task_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
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
            last_error = null,
            available_at = ?,
            updated_at = ?
        where task_id = ?
        """,
        (now, now, task_id),
    )


def _chunk_ids_for_audio_file(*, config: AppConfig, audio_file_id: str) -> list[str]:
    conn = connect(config.database_path)
    try:
        return _chunk_ids_for_audio_file_in_conn(conn, audio_file_id=audio_file_id)
    finally:
        conn.close()


def _chunk_ids_for_audio_file_in_conn(conn: sqlite3.Connection, *, audio_file_id: str) -> list[str]:
    rows = fetch_all(
        conn,
        "select chunk_id from audio_chunks where audio_file_id = ? order by source_start_ms",
        (audio_file_id,),
    )
    return [str(row["chunk_id"]) for row in rows]


def _ready_session_derive_dates(*, config: AppConfig, chunk_id: str) -> list[str]:
    conn = connect(config.database_path)
    try:
        return _ready_session_derive_dates_in_conn(conn, chunk_id=chunk_id)
    finally:
        conn.close()


def _ready_session_derive_dates_in_conn(conn: sqlite3.Connection, *, chunk_id: str) -> list[str]:
    rows = fetch_all(
        conn,
        """
        select ac.audio_file_id, substr(af.recorded_at, 1, 10) as date_key
        from audio_chunks ac
        join audio_files af on af.audio_file_id = ac.audio_file_id
        where ac.chunk_id = ?
        """,
        (chunk_id,),
    )
    if not rows:
        return []
    audio_file_id = str(rows[0]["audio_file_id"])
    # A chunk blocks session_derive only while its ASR work is still live; a chunk whose
    # asr task is failed_terminal or retry-exhausted is "done (failed)" and must not
    # block the file's sessions forever (liveness — mirrors the daily_generate fan-in).
    pending = fetch_all(
        conn,
        """
        select ac.chunk_id
        from audio_chunks ac
        left join tasks t
          on t.task_type = 'asr' and t.target_type = 'audio_chunk' and t.target_id = ac.chunk_id
        where ac.audio_file_id = ?
          and ac.status != 'transcribed'
          and (
            t.status is null
            or (
              t.status != 'failed_terminal'
              and not (t.status = 'failed_retryable' and t.retry_count >= t.max_retries)
            )
          )
        """,
        (audio_file_id,),
    )
    if pending:
        return []
    return sorted({str(row["date_key"]) for row in rows})


def _session_ids_for_day(*, config: AppConfig, day: str) -> list[str]:
    conn = connect(config.database_path)
    try:
        return _session_ids_for_day_in_conn(conn, day=day)
    finally:
        conn.close()


def _session_ids_for_day_in_conn(conn: sqlite3.Connection, *, day: str) -> list[str]:
    # `day` is the file recorded-day that session_derive ran for. Select sessions by
    # their first segment's file-day (NOT by date_key): a cross-midnight session has a
    # next-day date_key but is still produced by this file-day's derive, so keying on
    # date_key here would orphan it (never summarized/published).
    rows = fetch_all(
        conn,
        """
        select s.session_id
        from sessions s
        join transcript_segments ts on ts.segment_id = s.first_segment_id
        join audio_files af on af.audio_file_id = ts.audio_file_id
        where substr(af.recorded_at, 1, 10) = ?
        order by s.started_at
        """,
        (day,),
    )
    return [str(row["session_id"]) for row in rows]


def _ready_daily_generate_dates(*, config: AppConfig, session_id: str) -> list[str]:
    conn = connect(config.database_path)
    try:
        return _ready_daily_generate_dates_in_conn(conn, session_id=session_id)
    finally:
        conn.close()


def _ready_daily_generate_dates_in_conn(conn: sqlite3.Connection, *, session_id: str) -> list[str]:
    rows = fetch_all(conn, "select date_key from sessions where session_id = ?", (session_id,))
    if not rows:
        return []
    date_key = str(rows[0]["date_key"])
    # A summarize_session that has exhausted retries (failed_terminal) is "done" — it
    # must not block the day's daily report forever; daily_generate proceeds with the
    # sessions that did summarize.
    pending = fetch_all(
        conn,
        """
        select t.task_id
        from tasks t
        join sessions s on s.session_id = t.target_id
        where t.task_type = 'summarize_session'
          and s.date_key = ?
          and t.status not in ('succeeded', 'failed_terminal')
          and not (t.status = 'failed_retryable' and t.retry_count >= t.max_retries)
        """,
        (date_key,),
    )
    if pending:
        return []
    return [date_key]
