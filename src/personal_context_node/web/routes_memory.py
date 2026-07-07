"""记忆确认 API(design handoff Phase 5)— in-app confirm/reject/defer with Ed25519 signing."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.memory_review import (
    confirm_candidate,
    defer_candidate,
    list_candidates,
    reject_candidate,
    restore_candidate,
)

router = APIRouter(prefix="/api/memory")


class ConfirmRequest(BaseModel):
    edited_claim: str | None = None


@router.get("/candidates")
def get_candidates(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return list_candidates(config=config)


@router.post("/{candidate_id}/confirm")
def confirm(candidate_id: str, request: Request, payload: ConfirmRequest | None = None) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        receipt = confirm_candidate(
            config=config,
            candidate_id=candidate_id,
            edited_claim=payload.edited_claim if payload else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "candidate_id": receipt.candidate_id,
        "card_id": receipt.card_id,
        "event_type": receipt.event_type,
        "signature": receipt.signature,
        "note_path": receipt.note_path,
    }


def _mutate(request: Request, candidate_id: str, fn) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        fn(config=config, candidate_id=candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"candidate_id": candidate_id, "ok": True}


@router.post("/{candidate_id}/reject")
def reject(candidate_id: str, request: Request) -> dict[str, object]:
    return _mutate(request, candidate_id, reject_candidate)


@router.post("/{candidate_id}/defer")
def defer(candidate_id: str, request: Request) -> dict[str, object]:
    return _mutate(request, candidate_id, defer_candidate)


@router.post("/{candidate_id}/restore")
def restore(candidate_id: str, request: Request) -> dict[str, object]:
    return _mutate(request, candidate_id, restore_candidate)
