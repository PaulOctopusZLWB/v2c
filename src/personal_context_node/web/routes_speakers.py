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


class AssignPersonBulkRequest(BaseModel):
    speakers: list[str]
    person_id: str


def _assign_speaker_to_person(conn, *, speaker: str, person_id: str, person_label: str, now: str) -> None:
    """Map one speaker/cluster to a person (mirrors the single assign-person route).

    Ensures a speaker_clusters row exists (it carries a not-null label) then upserts the
    speaker_mapping; v_segment_attribution then collapses the cluster's segments onto the person.
    """
    conn.execute(
        "insert into speaker_clusters (speaker_cluster_id, label, source_type, source_ref, created_at) values (?, ?, ?, ?, ?) on conflict(speaker_cluster_id) do nothing",
        (speaker, speaker, "web_review", speaker, now),
    )
    upsert_speaker_mapping(conn, speaker=speaker, person_id=person_id, person_label=person_label, now=now, source="web_review")


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
        _assign_speaker_to_person(conn, speaker=speaker, person_id=payload.person_id, person_label=label, now=now)
        conn.commit()
    finally:
        conn.close()
    return {"speaker": speaker, "person_id": payload.person_id, "person_label": label}


@router.get("/speakers/clusters")
def list_speaker_clusters(request: Request, day: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    conn = connect(config.database_path)
    try:
        initialize(conn)
        clusters = fetch_all(
            conn,
            """
            with day_segments as (
              select
                ts.speaker_cluster_id,
                ts.segment_id,
                ts.text,
                ts.end_ms - ts.start_ms as speech_ms
              from transcript_segments ts
              join sessions sess on sess.session_id = ts.session_id
              where sess.date_key = ? and ts.is_active = 1
            )
            select
              ds.speaker_cluster_id,
              mapping.person_id as person_id,
              mapping.person_label as person_label,
              count(*) as segment_count,
              coalesce(sum(ds.speech_ms), 0) as total_speech_ms,
              (
                select s.segment_id from day_segments s
                where s.speaker_cluster_id = ds.speaker_cluster_id
                order by s.speech_ms desc, s.segment_id
                limit 1
              ) as sample_segment_id,
              (
                select s.text from day_segments s
                where s.speaker_cluster_id = ds.speaker_cluster_id
                order by s.speech_ms desc, s.segment_id
                limit 1
              ) as sample_text
            from day_segments ds
            left join speaker_mappings mapping on mapping.speaker_cluster_id = ds.speaker_cluster_id
            group by ds.speaker_cluster_id, mapping.person_id, mapping.person_label
            order by segment_count desc, ds.speaker_cluster_id
            """,
            (day,),
        )
    finally:
        conn.close()
    return {"clusters": clusters}


@router.post("/speakers/assign-person-bulk")
def assign_person_bulk_route(request: Request, payload: AssignPersonBulkRequest) -> dict[str, int]:
    if not payload.speakers:
        raise HTTPException(status_code=400, detail="speakers must not be empty")
    config: AppConfig = request.app.state.config
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        try:
            label = _person_label(conn, person_id=payload.person_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        for speaker in payload.speakers:
            _assign_speaker_to_person(conn, speaker=speaker, person_id=payload.person_id, person_label=label, now=now)
        conn.commit()
    finally:
        conn.close()
    return {"assigned": len(payload.speakers)}


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
