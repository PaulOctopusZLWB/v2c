from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.ingest import import_audio_files
from personal_context_node.tasks import process_status_rows, retry_task


router = APIRouter(prefix="/api/pipeline")
# SSE lives at /api/events per the API contract, NOT under /api/pipeline — so it gets its own router.
events_router = APIRouter(prefix="/api")


class ImportRequest(BaseModel):
    source_dir: str
    wait: bool = False  # default: import + enqueue, return immediately; the UI then calls /run


@router.post("/import")
def import_stage(request: Request, payload: ImportRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    result = import_audio_files(config=config, source_dir=Path(payload.source_dir))
    # Default path returns immediately so a long ASR run never blocks the request.
    response: dict[str, object] = {"imported_files": result.imported_files, "queued": True}
    if payload.wait:  # synchronous drain — tests / explicit "import and wait" only
        drain = request.app.state.worker.drain_now()
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
        # Emit an immediate snapshot, then poll for changes.
        for _ in range(10_000):
            if await request.is_disconnected():
                break
            rows = process_status_rows(config=config)
            signature = json.dumps([[r["task_id"], r["status"]] for r in rows], sort_keys=True)
            worker_running = worker.is_running()
            if signature != last_signature:
                last_signature = signature
                payload = {"tasks": rows, "worker_running": worker_running}
                yield "event: status.snapshot\n"
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            # Nothing is in flight and no worker is running: no further change can occur
            # without a new request, so close the stream (the EventSource reconnects later).
            if not worker_running and not any(r["status"] in _ACTIVE_TASK_STATUSES for r in rows):
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream")
