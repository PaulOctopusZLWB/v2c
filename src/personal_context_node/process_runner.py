from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.audio_preprocessing import preprocess_imported_audio
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.errors import TerminalPortError
from personal_context_node.core.ports.llm import LLMPort
from personal_context_node.core.ports.asr import ASRPort
from personal_context_node.core.ports.vad import VADPort
from personal_context_node.daily_reports import set_daily_report_status
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.obsidian_publish import publish_obsidian_day
from personal_context_node.session_summaries import summarize_session
from personal_context_node.sessions import derive_sessions_for_day
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
    task = claim_next_task(config=config, task_type="vad", run_id=run_id, lease_seconds=config.task_lease_seconds)
    if task is None:
        task = claim_next_task(config=config, task_type="asr", run_id=run_id, lease_seconds=config.task_lease_seconds)
    if task is None:
        task = claim_next_task(config=config, task_type="session_derive", run_id=run_id, lease_seconds=config.task_lease_seconds)
    if task is None:
        task = claim_next_task(config=config, task_type="summarize_session", run_id=run_id, lease_seconds=config.task_lease_seconds)
    if task is None:
        task = claim_next_task(config=config, task_type="daily_generate", run_id=run_id, lease_seconds=config.task_lease_seconds)
    if task is None:
        task = claim_next_task(config=config, task_type="obsidian_publish", run_id=run_id, lease_seconds=config.task_lease_seconds)
    if task is None:
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    try:
        start_task(config=config, task_id=task.task_id)
        if task.task_type == "vad":
            preprocess_imported_audio(config=config, vad=vad, max_chunk_ms=max_chunk_ms, audio_file_id=task.target_id)
        elif task.task_type == "asr":
            transcribe_pending_chunks(config=config, asr=asr, chunk_id=task.target_id)
        elif task.task_type == "session_derive":
            derive_sessions_for_day(config=config, day=task.target_id, session_gap_minutes=config.session_gap_minutes)
        elif task.task_type == "summarize_session":
            summarize_session(config=config, session_id=task.target_id, llm=llm_adapter)
            _succeed_task_and_enqueue_downstream(config=config, task_id=task.task_id, upstream_task_type=task.task_type, upstream_target_id=task.target_id)
            return ProcessOnceResult(task_id=task.task_id, task_type=task.task_type, status="succeeded")
        elif task.task_type == "daily_generate":
            set_daily_report_status(config=config, day=task.target_id, status="generating")
            generate_daily_context(config=config, day=task.target_id, llm=llm_adapter)
        elif task.task_type == "obsidian_publish":
            publish_obsidian_day(config=config, day=task.target_id, source_run_id=run_id)
        else:
            raise ValueError(f"unsupported task type: {task.task_type}")
        _succeed_task_and_enqueue_downstream(config=config, task_id=task.task_id, upstream_task_type=task.task_type, upstream_target_id=task.target_id)
        return ProcessOnceResult(task_id=task.task_id, task_type=task.task_type, status="succeeded")
    except Exception as exc:
        if task.task_type == "daily_generate":
            set_daily_report_status(config=config, day=task.target_id, status="failed", error=str(exc))
        fail_task(config=config, task_id=task.task_id, error=str(exc), terminal=isinstance(exc, TerminalPortError))
        raise


def _succeed_task_and_enqueue_downstream(
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
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            update tasks
            set status = 'succeeded',
                finished_at = ?,
                lease_expires_at = null,
                updated_at = ?
            where task_id = ?
            """,
            (now, now, task_id),
        )
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
            enqueue_task_in_conn(
                conn,
                task_type=edge.downstream_task_type,
                target_type=edge.downstream_target_type,
                target_id=target_id,
                max_retries=config.task_max_retries,
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
    pending = fetch_all(
        conn,
        "select chunk_id from audio_chunks where audio_file_id = ? and status != 'transcribed'",
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
    rows = fetch_all(conn, "select session_id from sessions where date_key = ? order by started_at", (day,))
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
    pending = fetch_all(
        conn,
        """
        select t.task_id
        from tasks t
        join sessions s on s.session_id = t.target_id
        where t.task_type = 'summarize_session'
          and s.date_key = ?
          and t.status != 'succeeded'
        """,
        (date_key,),
    )
    if pending:
        return []
    return [date_key]
