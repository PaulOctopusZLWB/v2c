"""Derivations for the /api/events SSE stream (design handoff Phase 4).

The stream keeps its 1s DB-polling model; this module turns two consecutive
polls into the richer event set the 管道控制室 consumes:

  - ``task.progress``       每次汇总变化时的紧凑进度(活跃任务 + done/total/ETA)。
  - ``segment.transcribed`` rowid 游标之后新落库的转写段(实时转写流)。
  - ``stage.changed``       活跃 task_type 切换。
  - ``task.failed``         新增的已定型失败任务。
  - ``run.completed``       本流观察到过活动、且一切定型 + worker 空闲。

纯函数 + 显式游标状态,便于单测;SSE 路由只是薄封装。
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize

# 每 tick 最多下发的新段数(防止批量回填时把浏览器打爆;多出的下一 tick 继续)。
MAX_SEGMENTS_PER_TICK = 50
_GPU_TASK_TYPES = frozenset({"vad", "transcribe_diarize", "asr", "extract_features"})


@dataclass
class EventCursor:
    """跨 tick 的推导状态(每个 SSE 连接一份)。"""

    last_stage: str | None = None
    failed_ids: frozenset[str] = frozenset()
    # transcript_segments 的 rowid 游标;None = 未初始化(连接时取 max,不回放历史)。
    segment_rowid: int | None = None
    saw_activity: bool = False
    initialized: bool = False


def max_segment_rowid(*, config: AppConfig, conn: sqlite3.Connection | None = None) -> int:
    owns_conn = conn is None
    if conn is None:
        conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select coalesce(max(rowid), 0) as m from transcript_segments")
    finally:
        if owns_conn:
            conn.close()
    return int(rows[0]["m"])


def fetch_new_segments(
    *, config: AppConfig, after_rowid: int, conn: sqlite3.Connection | None = None
) -> tuple[list[dict[str, object]], int]:
    """New active segments past the cursor (ordered by rowid), and the advanced cursor."""
    owns_conn = conn is None
    if conn is None:
        conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select rowid as _rowid, segment_id, session_id, text, speaker,
                   start_ms, end_ms, absolute_start_at, confidence
            from transcript_segments
            where rowid > ? and is_active = 1
            order by rowid
            limit ?
            """,
            (after_rowid, MAX_SEGMENTS_PER_TICK),
        )
    finally:
        if owns_conn:
            conn.close()
    cursor = int(rows[-1]["_rowid"]) if rows else after_rowid
    events = [
        {
            "segment_id": r["segment_id"],
            "session_id": r["session_id"],
            "text": r["text"],
            "speaker": r["speaker"],
            "start_ms": r["start_ms"],
            "end_ms": r["end_ms"],
            "absolute_start_at": r["absolute_start_at"],
            "confidence": r["confidence"],
        }
        for r in rows
    ]
    return events, cursor


def active_feature_progress(
    *, config: AppConfig, conn: sqlite3.Connection | None = None
) -> dict[str, object] | None:
    """Live artifact coverage for the active per-file feature extraction task.

    ``tasks`` only changes when the whole file finishes, which made a 5-20 minute
    extract_features task look frozen. The artifact tables commit every completed batch,
    so their coverage is a cheap, durable progress signal that also survives reconnects.
    ``elapsed_seconds`` acts as a heartbeat between batch commits.
    """
    owns_conn = conn is None
    if conn is None:
        conn = connect(config.database_path)
    try:
        initialize(conn)
        row = conn.execute(
            """
            select
              t.target_id,
              t.started_at,
              af.source_path,
              count(ts.segment_id) as total_segments,
              count(se.segment_id) as embedded,
              count(em.segment_id) as emoted
            from tasks t
            join audio_files af on af.audio_file_id = t.target_id
            left join transcript_segments ts
              on ts.audio_file_id = t.target_id and ts.is_active = 1
            left join segment_embeddings se on se.segment_id = ts.segment_id
            left join segment_emotions em on em.segment_id = ts.segment_id
            where t.task_type = 'extract_features'
              and t.status in ('claimed', 'running')
            group by t.task_id, t.target_id, t.started_at, af.source_path
            order by t.started_at
            limit 1
            """
        ).fetchone()
    finally:
        if owns_conn:
            conn.close()
    if row is None:
        return None

    total_segments = int(row["total_segments"] or 0)
    embedded = int(row["embedded"] or 0)
    emoted = int(row["emoted"] or 0)
    elapsed_seconds = 0
    started_at = str(row["started_at"] or "")
    if started_at:
        try:
            started = datetime.fromisoformat(started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed_seconds = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
        except ValueError:
            pass
    return {
        "active": True,
        "target_id": str(row["target_id"]),
        "current": Path(str(row["source_path"])).name,
        "total_segments": total_segments,
        "embedded": embedded,
        "emoted": emoted,
        "done": embedded + emoted,
        "total": total_segments * 2,
        "elapsed_seconds": elapsed_seconds,
    }


def estimate_pipeline_eta(
    *, rows: list[dict[str, object]], feature_progress: dict[str, object] | None
) -> tuple[int | None, str]:
    """Estimate queue ETA by task type and execution lane, not raw task count."""
    durations_by_type: dict[str, list[int]] = {}
    for row in rows:
        duration = row.get("duration_ms")
        if duration is not None:
            durations_by_type.setdefault(str(row["task_type"]), []).append(int(duration))

    lane_ms = {"gpu": 0.0, "cpu": 0.0}
    estimated_tasks = 0
    unknown_tasks = 0
    used_live_progress = False
    for row in rows:
        status = str(row["status"])
        if status not in {"pending", "claimed", "running", "failed_retryable"}:
            continue
        if status == "failed_retryable" and int(row.get("retry_count") or 0) >= int(row.get("max_retries") or 0):
            continue
        task_type = str(row["task_type"])
        samples = durations_by_type.get(task_type, [])
        if not samples:
            unknown_tasks += 1
            continue
        estimate_ms = float(statistics.median(samples))
        if (
            task_type == "extract_features"
            and status in {"claimed", "running"}
            and feature_progress
            and int(feature_progress.get("total") or 0) > 0
        ):
            total = int(feature_progress["total"])
            done = min(total, int(feature_progress.get("done") or 0))
            remaining_ratio = max(0.0, (total - done) / total)
            historical_remaining = estimate_ms * remaining_ratio
            elapsed_ms = int(feature_progress.get("elapsed_seconds") or 0) * 1000
            live_remaining = (elapsed_ms * (total - done) / done) if done > 0 else 0.0
            estimate_ms = max(historical_remaining, live_remaining)
            used_live_progress = done > 0
        lane = "gpu" if task_type in _GPU_TASK_TYPES else "cpu"
        lane_ms[lane] += estimate_ms
        estimated_tasks += 1

    if estimated_tasks == 0:
        return (None, "learning" if unknown_tasks else "none")
    confidence = "live" if used_live_progress and unknown_tasks == 0 else "historical" if unknown_tasks == 0 else "partial"
    return (max(1, round(max(lane_ms.values()) / 1000.0)), confidence)


def derive_tick_events(
    *,
    cursor: EventCursor,
    rows: list[dict[str, object]],
    summary: dict[str, object],
    summary_changed: bool,
    is_failed,
    new_segments: list[dict[str, object]],
) -> list[tuple[str, dict[str, object]]]:
    """One poll tick → ordered [(event_name, payload)]; mutates `cursor` in place."""
    events: list[tuple[str, dict[str, object]]] = []
    active_stage = summary.get("active_stage")
    worker_running = bool(summary.get("worker_running"))
    has_active = any(str(r["status"]) in ("pending", "claimed", "running") for r in rows)

    for seg in new_segments:
        events.append(("segment.transcribed", seg))

    # stage.changed:活跃阶段切换(含从 None 进入第一个阶段;退出到 None 由 run.completed 表达)。
    if active_stage is not None and active_stage != cursor.last_stage:
        events.append(
            ("stage.changed", {"stage": active_stage, "previous": cursor.last_stage, "target": summary.get("current_target")})
        )
    if active_stage is not None:
        cursor.last_stage = active_stage

    # task.failed:相对上一 tick 新增的定型失败。
    failed_now = frozenset(str(r["task_id"]) for r in rows if is_failed(r))
    if cursor.initialized:
        for r in rows:
            if str(r["task_id"]) in (failed_now - cursor.failed_ids):
                events.append(
                    (
                        "task.failed",
                        {
                            "task_id": r["task_id"],
                            "task_type": r["task_type"],
                            "target_id": r["target_id"],
                            "error": r.get("last_error"),
                        },
                    )
                )
    cursor.failed_ids = failed_now
    cursor.initialized = True

    if summary_changed and active_stage is not None:
        events.append(
            (
                "task.progress",
                {
                    "task_type": active_stage,
                    "target_id": summary.get("current_target"),
                    "done_total": summary.get("done_total"),
                    "total": summary.get("total"),
                    "eta_seconds": summary.get("eta_seconds"),
                    "feature_progress": summary.get("feature_progress"),
                },
            )
        )

    # run.completed:此前观察到过活动,现在全部定型且 worker 空闲。
    if worker_running or has_active or bool((summary.get("import_progress") or {}).get("active")):
        cursor.saw_activity = True
    elif cursor.saw_activity:
        cursor.saw_activity = False
        events.append(
            (
                "run.completed",
                {
                    "total": summary.get("total"),
                    "done_total": summary.get("done_total"),
                    "failed_total": summary.get("failed_total"),
                },
            )
        )

    return events
