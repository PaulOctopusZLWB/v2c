from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.ingest import import_audio_files
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
    # wait=True: synchronous import + drain (tests / explicit "import and wait" only).
    result = import_audio_files(config=config, source_dir=Path(payload.source_dir))
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


@events_router.get("/events")
async def events_stream(request: Request) -> StreamingResponse:
    config: AppConfig = request.app.state.config
    worker = request.app.state.worker

    async def stream():
        last_signature: str | None = None
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
            # Determine the active stage and current target from the first running/claimed task.
            active_stage: str | None = None
            current_target: str | None = None
            for r in rows:
                if r["status"] in ("claimed", "running"):
                    active_stage = str(r["task_type"])
                    current_target = str(r["target_id"])
                    break

            # Compact change-signature: (sorted status counts, import_progress)
            # We intentionally exclude task-level detail so the signature is small.
            signature = json.dumps(
                (sorted(status_counts.items()), import_progress),
                sort_keys=True,
                default=str,
            )
            if signature != last_signature:
                last_signature = signature
                payload = {
                    "status_counts": status_counts,
                    "total": total,
                    "active_stage": active_stage,
                    "current_target": current_target,
                    "import_progress": import_progress,
                    "worker_running": worker_running,
                }
                yield "event: status.summary\n"
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            # Nothing is in flight and no worker is running: no further change can occur
            # without a new request, so close the stream (the EventSource reconnects later).
            if not worker_running and not any(r["status"] in _ACTIVE_TASK_STATUSES for r in rows):
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream")
