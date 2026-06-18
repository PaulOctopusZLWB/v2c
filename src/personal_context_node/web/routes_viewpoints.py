from __future__ import annotations

from fastapi import APIRouter, Request

from personal_context_node.config import AppConfig
from personal_context_node.session_viewpoint import viewpoint_state


router = APIRouter(prefix="/api/sessions")


@router.get("/{session_id}/viewpoint")
def session_viewpoint_route(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return viewpoint_state(config=config, session_id=session_id)
