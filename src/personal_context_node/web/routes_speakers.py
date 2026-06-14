from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.speaker_review import upsert_segment_person_override, upsert_speaker_mapping
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


router = APIRouter(prefix="/api")


class AssignPersonRequest(BaseModel):
    person_id: str


class CreatePersonRequest(BaseModel):
    display_name: str
    person_type: str = "contact"


def _person_label(conn, *, person_id: str) -> str:
    rows = fetch_all(conn, "select display_name from persons where person_id = ?", (person_id,))
    if not rows:
        raise ValueError(f"unknown person_id: {person_id}")
    return str(rows[0]["display_name"])


@router.get("/persons")
def list_persons(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            "select person_id, display_name, person_type, is_self from persons order by is_self desc, display_name",
        )
    finally:
        conn.close()
    return {"persons": rows}


@router.post("/persons")
def create_person(request: Request, payload: CreatePersonRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    now = datetime.now(timezone.utc).isoformat()
    person_id = f"per_{uuid4().hex}"
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, 0, ?, ?)",
            (person_id, payload.display_name, payload.person_type, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"person_id": person_id, "display_name": payload.display_name, "person_type": payload.person_type, "is_self": 0}


@router.post("/speakers/{speaker}/assign-person")
def assign_speaker_route(request: Request, speaker: str, payload: AssignPersonRequest) -> dict[str, str]:
    config: AppConfig = request.app.state.config
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        try:
            label = _person_label(conn, person_id=payload.person_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # speaker_clusters has a not-null label; ensure a row exists for FK-free integrity.
        conn.execute(
            "insert into speaker_clusters (speaker_cluster_id, label, source_type, source_ref, created_at) values (?, ?, ?, ?, ?) on conflict(speaker_cluster_id) do nothing",
            (speaker, speaker, "web_review", speaker, now),
        )
        upsert_speaker_mapping(conn, speaker=speaker, person_id=payload.person_id, person_label=label, now=now, source="web_review")
        conn.commit()
    finally:
        conn.close()
    return {"speaker": speaker, "person_id": payload.person_id, "person_label": label}


@router.post("/transcripts/segments/{segment_id}/person-override")
def segment_override_route(request: Request, segment_id: str, payload: AssignPersonRequest) -> dict[str, str]:
    config: AppConfig = request.app.state.config
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        try:
            label = _person_label(conn, person_id=payload.person_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        upsert_segment_person_override(conn, segment_id=segment_id, person_id=payload.person_id, person_label=label, now=now)
        conn.commit()
    finally:
        conn.close()
    return {"segment_id": segment_id, "person_id": payload.person_id, "person_label": label}
