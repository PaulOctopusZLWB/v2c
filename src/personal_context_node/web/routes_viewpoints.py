from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.obsidian_sessions import publish_session_viewpoint
from personal_context_node.session_viewpoint import (
    DEFAULT_SESSION_PROMPT,
    clear_viewpoint_edit,
    effective_session_prompt,
    get_session_prompt_template,
    set_session_prompt_override,
    set_session_prompt_template,
    set_viewpoint_edit,
    viewpoint_state,
)
from personal_context_node.tasks import enqueue_task


router = APIRouter(prefix="/api/sessions")
prompts_router = APIRouter(prefix="/api/prompts")


class PromptRequest(BaseModel):
    # None/empty resets to the default (global) / clears (per-session override).
    template: str | None = None


class ViewpointEditRequest(BaseModel):
    # A full session_summary.v1 doc; validated server-side, 400 on failure.
    content: dict


@router.get("/{session_id}/viewpoint")
def session_viewpoint_route(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return viewpoint_state(config=config, session_id=session_id)


@router.put("/{session_id}/viewpoint")
def put_viewpoint_route(request: Request, session_id: str, body: ViewpointEditRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        set_viewpoint_edit(config=config, session_id=session_id, content=body.content)
    except Exception as exc:
        # validation failure -> 400 (the frontend shows the message; the result stays publishable).
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return viewpoint_state(config=config, session_id=session_id)


@router.delete("/{session_id}/viewpoint/edit")
def delete_viewpoint_edit_route(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    clear_viewpoint_edit(config=config, session_id=session_id)
    return viewpoint_state(config=config, session_id=session_id)


@router.post("/{session_id}/viewpoint/publish")
def publish_viewpoint_route(request: Request, session_id: str) -> dict[str, str]:
    config: AppConfig = request.app.state.config
    try:
        return publish_session_viewpoint(config=config, session_id=session_id)
    except ValueError as exc:
        # nothing generated/edited yet -> nothing to publish.
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
