"""AI 预审 (triage) API — read-only, rule-based binning for the review page."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from personal_context_node.config import AppConfig
from personal_context_node.triage import session_triage

router = APIRouter(prefix="/api")


@router.get("/sessions/{session_id}/triage")
def get_session_triage(session_id: str, request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    payload = session_triage(config=config, session_id=session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
    return payload
