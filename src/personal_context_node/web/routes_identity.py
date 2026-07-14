from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.identity_review import (
    identity_review_for_session,
    record_not_person,
    set_session_participant,
)
from personal_context_node.session_finalize import finalize_session
from personal_context_node.speaker_identify import cascade_participant_update, identify_session_speakers


router = APIRouter(prefix="/api")


class ParticipantRequest(BaseModel):
    person_id: str
    status: Literal["present", "absent", "uncertain"]
    note: str | None = None


class NotPersonRequest(BaseModel):
    session_id: str
    segment_ids: list[str]
    person_id: str
    note: str | None = None


class ConfirmCandidateRequest(BaseModel):
    session_id: str
    action: Literal["known_person", "new_person", "noise", "unknown"]
    person_id: str | None = None
    display_name: str | None = None
    segment_ids: list[str] = []
    note: str | None = None


@router.get("/sessions/{session_id}/identity-review")
def identity_review_route(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return identity_review_for_session(config=config, session_id=session_id)


@router.post("/sessions/{session_id}/participants")
def set_session_participant_route(
    request: Request, session_id: str, payload: ParticipantRequest
) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        participant = set_session_participant(
            config=config,
            session_id=session_id,
            person_id=payload.person_id,
            status=payload.status,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # The review verdict drives the data: absent -> clear that person's inferred attributions
    # and re-identify the session without them. (Synthesis/Obsidian is codex's layer — nothing
    # LLM-shaped runs here; the reviewer finishes by hitting 定稿/finalize.)
    cascade = cascade_participant_update(
        config=config, session_id=session_id, person_id=payload.person_id, status=payload.status
    )
    return {**participant, "cascade": cascade}


@router.post("/sessions/{session_id}/finalize")
def finalize_session_route(request: Request, session_id: str) -> dict[str, object]:
    """定稿:把会话事实冻结成 exports/sessions/ 下的 md+json 产物(codex 的输入合同)。

    Requires ≥1 present participant; idempotent (re-finalizing regenerates the files after
    attendance/attribution changes).
    """
    config: AppConfig = request.app.state.config
    try:
        return finalize_session(config=config, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/identify")
def identify_session_route(request: Request, session_id: str) -> dict[str, object]:
    """Manually re-trigger the automatic identify pass (match → prune → smooth → cluster).

    The review panel's "重新识别" button: re-runs with the CURRENT review constraints, so absent
    participants stay excluded and negative feedback keeps rejected pairs out.
    """
    config: AppConfig = request.app.state.config
    return identify_session_speakers(config=config, session_id=session_id)


@router.post("/identity/not-person")
def not_person_route(request: Request, payload: NotPersonRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        recorded = record_not_person(
            config=config,
            session_id=payload.session_id,
            segment_ids=payload.segment_ids,
            person_id=payload.person_id,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"recorded": recorded}


@router.post("/identity/confirm-candidate")
def confirm_candidate_route(request: Request, payload: ConfirmCandidateRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    if payload.action == "known_person":
        if not payload.person_id:
            raise HTTPException(status_code=400, detail="person_id is required for known_person")
        try:
            participant = set_session_participant(
                config=config,
                session_id=payload.session_id,
                person_id=payload.person_id,
                status="present",
                note=payload.note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"accepted": True, "action": payload.action, "participant": participant}
    return {"accepted": True, "action": payload.action}
