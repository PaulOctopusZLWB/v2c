from __future__ import annotations

from fastapi import APIRouter, Request

from personal_context_node.config import AppConfig
from personal_context_node.conversation_dynamics import session_dynamics


router = APIRouter(prefix="/api")


@router.get("/sessions/{session_id}/dynamics")
def session_dynamics_route(request: Request, session_id: str) -> dict[str, object]:
    """Per-session conversation dynamics: talk-share, turn-taking, timeline."""
    config: AppConfig = request.app.state.config
    return session_dynamics(config=config, session_id=session_id)
