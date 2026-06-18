from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.session_viewpoint import (
    DEFAULT_SESSION_PROMPT,
    effective_session_prompt,
    get_session_prompt_template,
    set_session_prompt_override,
    set_session_prompt_template,
    viewpoint_state,
)
from personal_context_node.tasks import enqueue_task


router = APIRouter(prefix="/api/sessions")
prompts_router = APIRouter(prefix="/api/prompts")


class PromptRequest(BaseModel):
    # None/empty resets to the default (global) / clears (per-session override).
    template: str | None = None


@router.get("/{session_id}/viewpoint")
def session_viewpoint_route(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return viewpoint_state(config=config, session_id=session_id)


@router.post("/{session_id}/viewpoint/generate")
def generate_viewpoint_route(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    # 404 when the session has no segments — there's nothing to summarize.
    if not viewpoint_state(config=config, session_id=session_id)["segments"]:
        raise HTTPException(status_code=404, detail=f"session has no segments: {session_id}")
    enqueue_task(
        config=config,
        task_type="summarize_session",
        target_type="session",
        target_id=session_id,
        priority=10,
    )
    request.app.state.worker.start()
    return {"enqueued": True, "session_id": session_id}


@router.put("/{session_id}/viewpoint/prompt")
def set_session_prompt_route(request: Request, session_id: str, body: PromptRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return set_session_prompt_override(config=config, session_id=session_id, template=body.template)


@prompts_router.get("/session_viewpoint")
def get_global_prompt_route(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {
        "template": get_session_prompt_template(config=config),
        "default": DEFAULT_SESSION_PROMPT,
    }


@prompts_router.put("/session_viewpoint")
def set_global_prompt_route(request: Request, body: PromptRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    set_session_prompt_template(config=config, template=body.template)
    return {
        "template": get_session_prompt_template(config=config),
        "default": DEFAULT_SESSION_PROMPT,
    }
