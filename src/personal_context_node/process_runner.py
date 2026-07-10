from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

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
from personal_context_node.jobs import record_job_run
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.obsidian_publish import publish_obsidian_day
from personal_context_node.obsidian_review import confirm_checked_candidates
from personal_context_node.session_summaries import summarize_session
from personal_context_node.sessions import derive_sessions_for_day
from personal_context_node.speaker_review import sync_speaker_review
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.tasks import claim_next_task, enqueue_task_in_conn, fail_task, reclaim_expired_tasks, start_task
from personal_context_node.transcription import transcribe_audio_file_diarized, transcribe_pending_chunks


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
    PipelineEdge("asr", "session_derive", "date_key", lambda conn, config, target_id: _ready_session_derive_dates_in_conn(conn, chunk_id=target_id, config=config)),
    # Diarize-mode sibling of the asr->session_derive edge: the whole-FILE transcribe_diarize
    # stage fans into session_derive once every same-day file has settled (round-7 invariant,
    # per audio_file). Only the active mode's tasks exist at runtime, so coexistence is safe.
    PipelineEdge("transcribe_diarize", "session_derive", "date_key", lambda conn, config, target_id: _ready_session_derive_dates_for_file_in_conn(conn, audio_file_id=target_id, config=config)),
    PipelineEdge("session_derive", "summarize_session", "session", lambda conn, config, target_id: _session_ids_for_day_in_conn(conn, day=target_id, config=config)),
    PipelineEdge("summarize_session", "daily_generate", "date_key", lambda conn, config, target_id: _ready_daily_generate_dates_in_conn(conn, session_id=target_id)),
    PipelineEdge("daily_generate", "obsidian_publish", "date_key", lambda _conn, _config, target_id: [target_id]),
)
# The pipeline's "viewpoint tail" — the three edges that auto-chain 观点 generation +
# daily report + Obsidian publish. When config.pipeline_auto_viewpoints is False (the
# default), these are NOT enqueued: the pipeline stops after session_derive and the tail
# stages are driven manually (per-session generate + manual publish). The two
# *→session_derive edges and vad→asr are always live.
_VIEWPOINT_TAIL_UPSTREAMS = frozenset({"session_derive", "summarize_session", "daily_generate"})
PROCESS_TASK_ORDER = (
    "vad",
    "transcribe_diarize",
    "session_derive",
    "summarize_session",
    "daily_generate",
    "obsidian_publish",
    "asr",
    "archive",
)
# Stages whose adapters own a single resident model subprocess (funasr_server & friends).
# Those adapters are NOT reentrant, so in a concurrent drain these types are pinned to one
# dedicated worker thread; everything else is safe to run in parallel (independent targets,
# per-call subprocess or pure-Python adapters).
GPU_TASK_TYPES = ("vad", "transcribe_diarize", "asr")
CPU_TASK_TYPES = tuple(t for t in PROCESS_TASK_ORDER if t not in GPU_TASK_TYPES)


def process_once(
    *,
    config: AppConfig,
    run_id: str,
    vad: VADPort,
    asr: ASRPort,
    llm: LLMPort | None = None,
    max_chunk_ms: int = 30_000,
    task_types: tuple[str, ...] | None = None,
    reclaim: bool = True,
) -> ProcessOnceResult:
    llm_adapter = llm or RuleBasedLLMAdapter()
    if reclaim:
        reclaim_expired_tasks(config=config, lease_seconds=config.task_lease_seconds)
    task = None
    for task_type in (task_types or PROCESS_TASK_ORDER):
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
        elif task.task_type == "transcribe_diarize":
            transcribe_audio_file_diarized(config=config, asr=asr, audio_file_id=task.target_id)
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
                -- Must mirror claim_next_task's ORDER BY exactly (priority first), or the
                -- dry-run preview reports a different "next task" than the one actually claimed
                -- whenever priority and availability disagree (the date-major scheduling case).
                order by priority, available_at, created_at
                limit 1
                """,
                (task_type, now),
            ).fetchone()
            if row is not None:
                return ProcessOnceResult(task_id=row["task_id"], task_type=row["task_type"], status="dry_run")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")
    finally:
        conn.close()


def _has_claimable_task(*, config: AppConfig, task_types: tuple[str, ...]) -> bool:
    """Read-only probe mirroring claim_next_task's WHERE clause (no lock, no claim)."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = datetime.now(timezone.utc).isoformat()
        for task_type in task_types:
            row = conn.execute(
                """
                select 1
                from tasks
                where task_type = ?
                  and (
                    status = 'pending'
                    or (status = 'failed_retryable' and retry_count < max_retries)
                  )
                  and available_at <= ?
                limit 1
                """,
                (task_type, now),
            ).fetchone()
            if row is not None:
                return True
        return False
    finally:
        conn.close()


@dataclass(frozen=True)
class DrainResult:
    process_steps: int
    tasks_succeeded: int
    tasks_failed: int
    status: str  # "complete" | "stopped" | "step_limit"


def drain_process_queue(
    *,
    config: AppConfig,
    vad: VADPort,
    asr: ASRPort,
    llm: LLMPort | None = None,
    max_chunk_ms: int | None = None,
    max_steps: int = 200,
    should_stop: Callable[[], bool] = lambda: False,
    job_name: str = "process.drain",
    workers: int | None = None,
) -> DrainResult:
    chunk_ms = max_chunk_ms if max_chunk_ms is not None else config.max_chunk_ms
    worker_count = workers if workers is not None else max(1, int(getattr(config, "pipeline_workers", 1) or 1))
    if worker_count > 1:
        return _drain_concurrent(
            config=config,
            vad=vad,
            asr=asr,
            llm=llm,
            chunk_ms=chunk_ms,
            max_steps=max_steps,
            should_stop=should_stop,
            job_name=job_name,
            worker_count=worker_count,
        )
    process_steps = 0
    tasks_succeeded = 0
    tasks_failed = 0
    status = "step_limit"
    while process_steps < max_steps:
        if should_stop():
            status = "stopped"
            break
        run_id = f"run_{uuid4().hex}"
        try:
            result = record_job_run(
                config=config,
                job_name=job_name,
                run_id=run_id,
                operation=lambda run_id=run_id: process_once(
                    config=config, run_id=run_id, vad=vad, asr=asr, llm=llm, max_chunk_ms=chunk_ms
                ),
            ).result
        except Exception:
            # A single task failure (transient port error, schema-validation, etc.) is
            # isolated and retryable (§36) — it must not abort the whole drain. process_once
            # already marked the task failed (with backoff deferral), so count it and keep
            # draining so independent files/days still get processed this run.
            tasks_failed += 1
            process_steps += 1
            continue
        if result.status == "no_task":
            status = "complete"
            break
        process_steps += 1
        if result.status == "succeeded":
            tasks_succeeded += 1
    if status == "step_limit" and preview_next_process_task(config=config).status == "no_task":
        status = "complete"
    return DrainResult(
        process_steps=process_steps,
        tasks_succeeded=tasks_succeeded,
        tasks_failed=tasks_failed,
        status=status,
    )


def _drain_concurrent(
    *,
    config: AppConfig,
    vad: VADPort,
    asr: ASRPort,
    llm: LLMPort | None,
    chunk_ms: int,
    max_steps: int,
    should_stop: Callable[[], bool],
    job_name: str,
    worker_count: int,
) -> DrainResult:
    """Multi-threaded drain. Thread 0 owns the GPU stages (resident-model adapters are
    single-subprocess, non-reentrant); threads 1..n-1 run the CPU stages in parallel.
    Lease-based claiming (begin immediate + claimed_by_run_id guard) already makes
    concurrent claims safe; WAL + busy_timeout serialize the commits.

    A thread exits only when its own claim comes back empty AND no other thread is
    executing (twice in a row, with a sleep between) — an executing task may still fan
    out downstream work, so "my queue is empty" alone is not "the drain is done".
    Downstream enqueues commit before the executor is marked idle, so the exit check
    cannot race past freshly fanned-out work.
    """
    reclaim_expired_tasks(config=config, lease_seconds=config.task_lease_seconds)
    lock = threading.Lock()
    state = {"steps": 0, "succeeded": 0, "failed": 0, "executing": 0, "stopped": False}

    def _loop(task_types: tuple[str, ...]) -> None:
        idle_rounds = 0
        while True:
            if should_stop():
                with lock:
                    state["stopped"] = True
                return
            with lock:
                if state["stopped"] or state["steps"] >= max_steps:
                    return
            # Cheap read-only probe first: an idle thread polling every 0.2s must not
            # insert a job_runs row (record_job_run) per poll while a sibling grinds
            # through a long ASR task.
            outcome = "no_task"
            if _has_claimable_task(config=config, task_types=task_types):
                with lock:
                    state["executing"] += 1
                run_id = f"run_{uuid4().hex}"
                try:
                    result = record_job_run(
                        config=config,
                        job_name=job_name,
                        run_id=run_id,
                        operation=lambda run_id=run_id: process_once(
                            config=config, run_id=run_id, vad=vad, asr=asr, llm=llm,
                            max_chunk_ms=chunk_ms, task_types=task_types, reclaim=False,
                        ),
                    ).result
                    outcome = result.status  # "no_task" | "succeeded"
                except Exception:
                    # Same isolation as the single-threaded drain: process_once already marked
                    # the task failed (with backoff); count it and keep draining.
                    outcome = "task_failed"
                finally:
                    with lock:
                        state["executing"] -= 1
            if outcome == "no_task":
                # Exit only after two consecutive empty probes with every executor idle.
                # Fan-out commits happen-before the executor decrements `executing`, so a
                # freshly fanned-out task is always visible to the second probe.
                with lock:
                    others_busy = state["executing"] > 0
                if others_busy:
                    idle_rounds = 0
                else:
                    idle_rounds += 1
                    if idle_rounds >= 2:
                        return
                time.sleep(0.2)
                continue
            idle_rounds = 0
            with lock:
                state["steps"] += 1
                if outcome == "succeeded":
                    state["succeeded"] += 1
                else:
                    state["failed"] += 1

    gpu_order = tuple(t for t in PROCESS_TASK_ORDER if t in GPU_TASK_TYPES)
    cpu_order = tuple(t for t in PROCESS_TASK_ORDER if t not in GPU_TASK_TYPES)
    thread_types: list[tuple[str, ...]] = [gpu_order]
    thread_types.extend([cpu_order] * (worker_count - 1))
    threads = [
        threading.Thread(target=_loop, args=(types,), daemon=True, name=f"drain-{i}")
        for i, types in enumerate(thread_types)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if state["stopped"]:
        status = "stopped"
    elif preview_next_process_task(config=config).status == "no_task":
        status = "complete"
    else:
        status = "step_limit"
    return DrainResult(
        process_steps=state["steps"],
        tasks_succeeded=state["succeeded"],
        tasks_failed=state["failed"],
        status=status,
    )


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
    # Carry the upstream task's priority forward so the whole day's pipeline (asr →
    # session_derive → … → obsidian_publish) inherits the date ordinal and stays in
    # date order relative to other days.
    upstream_row = conn.execute(
        "select priority from tasks where task_type = ? and target_id = ? order by created_at limit 1",
        (upstream_task_type, upstream_target_id),
    ).fetchone()
    upstream_priority: int = int(upstream_row["priority"]) if upstream_row is not None else 100
    # Cut the auto-chain when manual mode is on: filter the viewpoint-tail edges by config
    # at enqueue time (never mutate the module-level PIPELINE list). A manually enqueued
    # summarize_session still runs, but won't fan out to daily_generate when the flag is off.
    auto_viewpoints = config.pipeline_auto_viewpoints
    for edge in PIPELINE:
        if edge.upstream_task_type != upstream_task_type:
            continue
        if not auto_viewpoints and edge.upstream_task_type in _VIEWPOINT_TAIL_UPSTREAMS:
            continue
        for target_id in edge.target_ids(conn, config, upstream_target_id):
            result = enqueue_task_in_conn(
                conn,
                task_type=edge.downstream_task_type,
                target_type=edge.downstream_target_type,
                target_id=target_id,
                max_retries=config.task_max_retries,
                priority=upstream_priority,
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
        return _ready_session_derive_dates_in_conn(conn, chunk_id=chunk_id, config=config)
    finally:
        conn.close()


def _ready_session_derive_dates_in_conn(
    conn: sqlite3.Connection, *, chunk_id: str, config: AppConfig | None = None
) -> list[str]:
    rows = fetch_all(
        conn,
        """
        select substr(af.recorded_at, 1, 10) as date_key
        from audio_chunks ac
        join audio_files af on af.audio_file_id = ac.audio_file_id
        where ac.chunk_id = ?
        """,
        (chunk_id,),
    )
    if not rows:
        return []
    date_key = str(rows[0]["date_key"])
    # session_derive (and everything downstream of it — summarize_session, daily_generate,
    # obsidian_publish) consumes the WHOLE day: derive_sessions_for_day rebuilds from every
    # same-day file's segments. So the fan-in must wait until every chunk of EVERY audio file
    # recorded on this day has finished ASR — not just the triggering chunk's own file. Gating
    # per-file caused a premature, partial derive+publish for the first-completed recording on a
    # multi-recording day (the common case — recorded_at carries HHMMSS), then a redundant full
    # re-derive/re-summarize/re-publish once the later recordings transcribed. A chunk whose asr
    # is failed_terminal or retry-exhausted is "done (failed)" and must not block forever.
    pending = fetch_all(
        conn,
        """
        select ac.chunk_id
        from audio_chunks ac
        join audio_files af on af.audio_file_id = ac.audio_file_id
        left join tasks t
          on t.task_type = 'asr' and t.target_type = 'audio_chunk' and t.target_id = ac.chunk_id
        where substr(af.recorded_at, 1, 10) = ?
          and ac.status != 'transcribed'
          and (
            t.status is null
            or (
              t.status != 'failed_terminal'
              and not (t.status = 'failed_retryable' and t.retry_count >= t.max_retries)
            )
          )
        """,
        (date_key,),
    )
    if pending:
        return []
    return [date_key]


def _day_has_unidentified_speakers_in_conn(conn: sqlite3.Connection, *, date_key: str) -> bool:
    """True if any active segment from that file-day has no person attribution yet.

    Mirrors v_segment_attribution (override.person_id, else cluster mapping.person_id). The
    speaker-first gate (require_identified_speakers) uses this to hold a day at the ASR→
    session_derive edge until every voice that day is identified — diarize labels (spk_NN) are
    per-file and collide across files, so identity must come from per-segment attribution, not
    the raw label.
    """
    rows = fetch_all(
        conn,
        """
        select 1
        from transcript_segments ts
        join audio_files af on af.audio_file_id = ts.audio_file_id
        join v_segment_attribution va on va.segment_id = ts.segment_id
        where substr(af.recorded_at, 1, 10) = ?
          and ts.is_active = 1
          and va.person_id is null
        limit 1
        """,
        (date_key,),
    )
    return bool(rows)


def _ready_session_derive_dates_for_file(*, config: AppConfig, audio_file_id: str) -> list[str]:
    conn = connect(config.database_path)
    try:
        return _ready_session_derive_dates_for_file_in_conn(conn, audio_file_id=audio_file_id, config=config)
    finally:
        conn.close()


def _ready_session_derive_dates_for_file_in_conn(
    conn: sqlite3.Connection, *, audio_file_id: str, config: AppConfig | None = None
) -> list[str]:
    rows = fetch_all(
        conn,
        "select substr(recorded_at,1,10) as date_key from audio_files where audio_file_id = ?",
        (audio_file_id,),
    )
    if not rows:
        return []
    date_key = str(rows[0]["date_key"])
    # The day is ready for session_derive only when EVERY audio file recorded that day has a
    # transcribe_diarize task that is settled (succeeded, or terminally/retry-exhausted failed).
    # A file with no task yet, or still pending/running/retryable-with-retries, blocks the day
    # (the round-7 whole-day invariant, re-expressed per audio_file).
    pending = fetch_all(
        conn,
        """
        select af.audio_file_id
        from audio_files af
        left join tasks t
          on t.task_type = 'transcribe_diarize' and t.target_type = 'audio_file'
             and t.target_id = af.audio_file_id
        where substr(af.recorded_at,1,10) = ?
          and (
            t.status is null
            or (t.status not in ('succeeded','failed_terminal')
                and not (t.status = 'failed_retryable' and t.retry_count >= t.max_retries))
          )
    """,
        (date_key,),
    )
    if pending:
        return []
    return [date_key]


def _session_ids_for_day(*, config: AppConfig, day: str) -> list[str]:
    conn = connect(config.database_path)
    try:
        return _session_ids_for_day_in_conn(conn, day=day, config=config)
    finally:
        conn.close()


def _session_ids_for_day_in_conn(conn: sqlite3.Connection, *, day: str, config: AppConfig | None = None) -> list[str]:
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
    # Speaker-first gate: sessions exist (so the review UI can list/clusters/listen), but the
    # day's content is NOT summarized until every voice that day is attributed to a person.
    if config is not None and config.require_identified_speakers and _day_has_unidentified_speakers_in_conn(conn, date_key=day):
        return []
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
