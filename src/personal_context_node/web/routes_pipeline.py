from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.ingest import import_audio_files
from personal_context_node.pipeline_events import EventCursor, derive_tick_events, fetch_new_segments, max_segment_rowid
from personal_context_node.tasks import process_status_rows, retry_failed_tasks, retry_task


router = APIRouter(prefix="/api/pipeline")
# SSE lives at /api/events per the API contract, NOT under /api/pipeline — so it gets its own router.
events_router = APIRouter(prefix="/api")


class ImportRequest(BaseModel):
    source_dir: str
    wait: bool = False  # default: import + enqueue, return immediately; the UI then calls /run


@router.post("/import")
def import_stage(request: Request, payload: ImportRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    worker = request.app.state.worker
    if not payload.wait:
        # Non-blocking: hand the (possibly multi-GB) copy to a background thread so the
        # request returns immediately and the UI can render a live progress bar via SSE.
        started = worker.start_import(payload.source_dir)
        return {"started": started, "importing": True}
    # wait=True: synchronous import + drain (tests / explicit "import and wait" only). Use the
    # effective config so an asr_mode override routes the import to the right task_type too.
    from personal_context_node import settings as _settings

    result = import_audio_files(config=_settings.effective_config(config), source_dir=Path(payload.source_dir))
    response: dict[str, object] = {"imported_files": result.imported_files, "queued": True}
    drain = worker.drain_now()
    response["drain"] = {
        "status": drain.status,
        "process_steps": drain.process_steps,
        "tasks_succeeded": drain.tasks_succeeded,
    }
    return response


@router.post("/run")
def run_stage(request: Request) -> dict[str, object]:
    started = request.app.state.worker.start()
    return {"worker_started": started, "worker_running": request.app.state.worker.is_running()}


@router.post("/stop")
def stop_stage(request: Request) -> dict[str, object]:
    request.app.state.worker.request_stop()
    return {"stop_requested": True, "worker_running": request.app.state.worker.is_running()}


@router.post("/tasks/{task_id}/retry")
def retry_task_route(request: Request, task_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        result = retry_task(config=config, task_id=task_id)
    except ValueError as exc:  # tasks.retry_task raises ValueError("task not found: ...")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # RetryTaskResult is exactly (task_id, status) — see tasks.py:29-32.
    return {"task_id": result.task_id, "status": result.status}


@router.post("/retry-failed")
def retry_failed_route(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    count = retry_failed_tasks(config=config)
    return {"retried": count}


# Task statuses that mean "more is still going to happen on its own" — the SSE stream
# stays open while any task is in flight or the worker is running. Once everything is
# terminal AND the worker is idle, the stream closes after its final snapshot and the
# browser EventSource reconnects on the next action. (This also lets the buffering
# TestClient — which materializes the whole body before returning — complete on a fresh
# DB instead of blocking forever on an endless generator.)
_ACTIVE_TASK_STATUSES = frozenset({"pending", "claimed", "running"})


def _is_settled(row: dict[str, object]) -> bool:
    """A task that won't progress on its own: succeeded, terminally failed, or a retryable
    failure that has exhausted its retries (the claimer skips it once retry_count >= max_retries,
    so it is effectively terminal even though its status string is still 'failed_retryable')."""
    status = str(row["status"])
    if status in ("succeeded", "failed_terminal"):
        return True
    if status == "failed_retryable":
        return int(row["retry_count"]) >= int(row["max_retries"])
    return False


def _is_failed(row: dict[str, object]) -> bool:
    """A settled task that ended in failure (a subset of _is_settled — excludes 'succeeded')."""
    return _is_settled(row) and str(row["status"]) != "succeeded"


@events_router.get("/events")
async def events_stream(request: Request) -> StreamingResponse:
    config: AppConfig = request.app.state.config
    worker = request.app.state.worker

    async def stream():
        last_signature: str | None = None
        # 事件游标(每连接一份):新段 rowid / 上个活跃阶段 / 已知失败集 / 是否观察到活动。
        cursor = EventCursor(segment_rowid=max_segment_rowid(config=config))
        # Emit an immediate compact summary, then poll for changes.
        for _ in range(10_000):
            if await request.is_disconnected():
                break
            rows = process_status_rows(config=config)
            import_progress = worker.import_state()
            worker_running = worker.is_running()

            # Build a compact status summary (counts + max updated_at) — much smaller
            # than the full 1881-row task array that was previously serialised every tick.
            from collections import Counter
            status_counts = dict(Counter(str(r["status"]) for r in rows))
            total = len(rows)
            # Per-stage done/total + done/failed totals + an ETA, so the always-visible header
            # can show the breakdown and remaining time WITHOUT fetching the full task list.
            # "done" = settled (will not progress on its own); "failed" = settled-with-failure.
            stage_counts: dict[str, dict[str, int]] = {}
            done_total = 0
            failed_total = 0
            for r in rows:
                bucket = stage_counts.setdefault(str(r["task_type"]), {"done": 0, "total": 0})
                bucket["total"] += 1
                if _is_settled(r):
                    bucket["done"] += 1
                    done_total += 1
                    if _is_failed(r):
                        failed_total += 1
            durations = [int(r["duration_ms"]) for r in rows if r["duration_ms"] is not None]
            remaining = total - done_total
            eta_seconds = round(remaining * (sum(durations) / len(durations)) / 1000.0) if durations and remaining > 0 else None
            # Determine the active stage and current target from the first running/claimed task.
            active_stage: str | None = None
            current_target: str | None = None
            for r in rows:
                if r["status"] in ("claimed", "running"):
                    active_stage = str(r["task_type"])
                    current_target = str(r["target_id"])
                    break

            # Compact change-signature: every field that the rendered payload shows must be
            # part of it, or a transition that changes only that field (e.g. the worker going
            # idle after the last task settled, or the active stage/target advancing) would be
            # silently dropped and the UI would freeze on a stale frame. Still tiny — counts,
            # not per-task detail.
            signature = json.dumps(
                (sorted(status_counts.items()), worker_running, active_stage, current_target, import_progress),
                sort_keys=True,
                default=str,
            )
            payload = {
                "status_counts": status_counts,
                "total": total,
                "stage_counts": stage_counts,
                "done_total": done_total,
                "failed_total": failed_total,
                "eta_seconds": eta_seconds,
                "active_stage": active_stage,
                "current_target": current_target,
                "import_progress": import_progress,
                "worker_running": worker_running,
            }
            summary_changed = signature != last_signature
            if summary_changed:
                last_signature = signature
                yield "event: status.summary\n"
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

            # 管道控制室的细粒度事件:新段 / 阶段切换 / 新失败 / 进度 / 收尾。
            new_segments, cursor.segment_rowid = fetch_new_segments(
                config=config, after_rowid=cursor.segment_rowid or 0
            )
            for name, data in derive_tick_events(
                cursor=cursor,
                rows=rows,
                summary=payload,
                summary_changed=summary_changed,
                is_failed=_is_failed,
                new_segments=new_segments,
            ):
                yield f"event: {name}\n"
                yield f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

            # Nothing is in flight and no worker is running: no further change can occur
            # without a new request, so close the stream (the EventSource reconnects later).
            # An active import counts as in-flight too — otherwise the stream could close on a
            # tick where tasks settle while a copy is still running, dropping the run.completed
            # event (derive_tick_events keeps saw_activity latched during import).
            import_active = bool(import_progress and import_progress.get("active"))
            if not worker_running and not import_active and not any(r["status"] in _ACTIVE_TASK_STATUSES for r in rows):
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream")
