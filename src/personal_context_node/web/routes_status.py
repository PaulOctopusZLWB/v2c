from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Request

from personal_context_node.config import AppConfig
from personal_context_node.jobs import job_status_rows
from personal_context_node.tasks import process_status_rows


router = APIRouter(prefix="/api/status")


@router.get("/tasks")
def status_tasks(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"tasks": process_status_rows(config=config)}


@router.get("/runs")
def status_runs(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"runs": job_status_rows(config=config)}


@router.get("/overview")
def status_overview(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    rows = process_status_rows(config=config)
    counts = Counter(str(row["status"]) for row in rows)
    worker = getattr(request.app.state, "worker", None)
    return {
        "worker_running": bool(worker.is_running()) if worker is not None else False,
        "status_counts": dict(counts),
        "total_tasks": len(rows),
        "import_progress": worker.import_state() if worker is not None else None,
    }
