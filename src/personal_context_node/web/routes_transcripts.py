from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.session_viewpoint import set_segment_text
from personal_context_node.transcript_review import (
    accept_remaining_segments,
    batch_review_segments,
    clear_review_segments,
    day_status_rows,
    delete_session,
    list_days,
    rename_session,
    review_queue,
    review_segment,
    reviewed_segments_for_session,
    search_transcripts,
    session_review_status,
    sessions_for_day,
    session_name,
)


router = APIRouter(prefix="/api/transcripts")


class ReviewSegmentRequest(BaseModel):
    status: str
    note: str = ""


class BatchReviewRequest(BaseModel):
    segment_ids: list[str]
    status: str
    note: str = ""


class ClearReviewRequest(BaseModel):
    segment_ids: list[str]


class RenameSessionRequest(BaseModel):
    name: str


class SegmentTextRequest(BaseModel):
    text: str


@router.get("/day-status")
def transcript_day_status(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"days": day_status_rows(config=config)}


@router.get("/days")
def transcript_days(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"days": list_days(config=config)}


@router.get("/review-queue")
def transcript_review_queue(request: Request, limit: int = 100) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"queue": review_queue(config=config, limit=limit)}


@router.get("/search")
def search_transcript_segments(request: Request, q: str = "", limit: int = 30) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"results": search_transcripts(config=config, query=q, limit=limit)}


@router.get("/days/{day}/sessions")
def transcript_day_sessions(request: Request, day: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"day": day, "sessions": sessions_for_day(config=config, day=day)}


@router.get("/sessions/{session_id}")
def session_transcript(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {
        "session_id": session_id,
        "name": session_name(config=config, session_id=session_id),
        "review_status": session_review_status(config=config, session_id=session_id),
        "segments": reviewed_segments_for_session(config=config, session_id=session_id),
    }


@router.put("/sessions/{session_id}/name")
def rename_session_route(request: Request, session_id: str, payload: RenameSessionRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    if not rename_session(config=config, session_id=session_id, name=payload.name):
        raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
    return {"session_id": session_id, "name": payload.name.strip() or None}


@router.delete("/sessions/{session_id}")
def delete_session_route(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    result = delete_session(config=config, session_id=session_id)
    if not result["deleted"]:
        raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
    return result


@router.patch("/segments/{segment_id}")
def patch_segment_text_route(request: Request, segment_id: str, payload: SegmentTextRequest) -> dict[str, str]:
    config: AppConfig = request.app.state.config
    if not set_segment_text(config=config, segment_id=segment_id, text=payload.text):
        raise HTTPException(status_code=404, detail=f"unknown segment: {segment_id}")
    return {"segment_id": segment_id, "text": payload.text.strip()}


@router.post("/segments/{segment_id}/review")
def review_segment_route(request: Request, segment_id: str, payload: ReviewSegmentRequest) -> dict[str, str]:
    config: AppConfig = request.app.state.config
    try:
        review_segment(config=config, segment_id=segment_id, status=payload.status, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"segment_id": segment_id, "status": payload.status}


@router.post("/segments/batch-review")
def batch_review_route(request: Request, payload: BatchReviewRequest) -> dict[str, int]:
    config: AppConfig = request.app.state.config
    if not payload.segment_ids:
        raise HTTPException(status_code=400, detail="segment_ids must not be empty")
    try:
        updated = batch_review_segments(
            config=config,
            segment_ids=payload.segment_ids,
            status=payload.status,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"updated": updated}


@router.post("/segments/clear-review")
def clear_review_route(request: Request, payload: ClearReviewRequest) -> dict[str, int]:
    config: AppConfig = request.app.state.config
    if not payload.segment_ids:
        raise HTTPException(status_code=400, detail="segment_ids must not be empty")
    cleared = clear_review_segments(config=config, segment_ids=payload.segment_ids)
    return {"cleared": cleared}


@router.post("/sessions/{session_id}/accept-remaining")
def accept_remaining_route(request: Request, session_id: str) -> dict[str, int]:
    config: AppConfig = request.app.state.config
    return accept_remaining_segments(config=config, session_id=session_id)
