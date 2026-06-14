from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from personal_context_node.config import AppConfig
from personal_context_node.llm_results import daily_context, day_memory_candidates, session_summary


router = APIRouter(prefix="/api/llm")


@router.get("/sessions/{session_id}/summary")
def session_summary_route(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    result = session_summary(config=config, session_id=session_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no session summary: {session_id}")
    return result


@router.get("/days/{day}")
def daily_route(request: Request, day: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {
        "day": day,
        "context": daily_context(config=config, day=day),
        "memory_candidates": day_memory_candidates(config=config, day=day),
    }
