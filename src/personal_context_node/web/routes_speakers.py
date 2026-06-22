from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.segment_emotions import emotion_distribution, emotion_labels_for_scope
from personal_context_node.speaker_embeddings import (
    auto_attribute_enrolled,
    clear_projection_cache,
    cluster_voiceprints,
    embedding_projection,
    mark_noise_segments,
    enroll_person,
    label_segments_as_person,
    project_embeddings,
    recluster_by_anchors,
    suggest_people_for_session,
)
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


class ReclusterRequest(BaseModel):
    anchors: dict[str, str]
    threshold: float = 0.5
    session_id: str | None = None
    day: str | None = None


class AutoClusterRequest(BaseModel):
    min_cluster_size: int = 30
    session_id: str | None = None
    day: str | None = None


class MarkNoiseRequest(BaseModel):
    filler: bool = False
    max_duration_ms: int | None = None
    noise_person_id: str | None = None
    session_id: str | None = None
    day: str | None = None


class ExtractEmbeddingsRequest(BaseModel):
    session_id: str | None = None
    day: str | None = None


class ExtractEmotionsRequest(BaseModel):
    session_id: str | None = None
    day: str | None = None


class LabelSegmentsRequest(BaseModel):
    segment_ids: list[str]


class EnrollPersonRequest(BaseModel):
    segment_ids: list[str] | None = None


class SuggestPeopleRequest(BaseModel):
    session_id: str


class AutoAttributeRequest(BaseModel):
    session_id: str | None = None
    day: str | None = None
    threshold: float = 0.5


class MergePeopleRequest(BaseModel):
    from_id: str
    into_id: str


class ProjectionRequest(BaseModel):
    session_ids: list[str] = []
    days: list[str] = []
    method: str = "umap"
    n_neighbors: int = 15
    min_dist: float = 0.1
    pca_x: int = 0
    pca_y: int = 1
    perplexity: int = 30
    max_points: int = 4000


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


def _delete_person(conn, *, person_id: str) -> None:
    """Remove a person and every row that references them, in the caller's transaction.

    Deletes the person's segment_person_overrides, person_voiceprints, and speaker_mappings;
    nulls sessions.primary_person_id pointing at them; then deletes the persons row. The caller
    has already verified the person exists and is responsible for the commit.
    """
    conn.execute("delete from segment_person_overrides where person_id = ?", (person_id,))
    conn.execute("delete from person_voiceprints where person_id = ?", (person_id,))
    conn.execute("delete from speaker_mappings where person_id = ?", (person_id,))
    conn.execute("update sessions set primary_person_id = null where primary_person_id = ?", (person_id,))
    conn.execute("delete from persons where person_id = ?", (person_id,))


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


@router.delete("/persons/{person_id}")
def delete_person_route(request: Request, person_id: str) -> dict[str, bool]:
    """Delete a person (e.g. an accidental duplicate) and cascade across every referencing table.

    All deletions/nulling happen in one transaction; 404 if the person does not exist. The 2D
    voiceprint map colors segments by person, so clear its projection cache afterward.
    """
    config: AppConfig = request.app.state.config
    conn = connect(config.database_path)
    try:
        initialize(conn)
        if not fetch_all(conn, "select 1 from persons where person_id = ?", (person_id,)):
            raise HTTPException(status_code=404, detail=f"unknown person_id: {person_id}")
        _delete_person(conn, person_id=person_id)
        conn.commit()
    finally:
        conn.close()
    clear_projection_cache()  # a person's segments lose their color -> any cached projection is stale
    return {"deleted": True}


@router.post("/people/merge")
def merge_people_route(request: Request, payload: MergePeopleRequest) -> dict[str, int]:
    """Merge a duplicate person (from_id) into another (into_id) without losing labels.

    Reassigns from_id's segment_person_overrides (person_id + person_label) and speaker_mappings to
    into_id, then deletes from_id (cascading the rest). 404 if either is missing; 400 if from==into.
    Returns the number of attribution rows moved (overrides + mappings).
    """
    if payload.from_id == payload.into_id:
        raise HTTPException(status_code=400, detail="from_id and into_id must differ")
    config: AppConfig = request.app.state.config
    conn = connect(config.database_path)
    try:
        initialize(conn)
        try:
            into_label = _person_label(conn, person_id=payload.into_id)
            _person_label(conn, person_id=payload.from_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # Reassign from_id's labels to into_id (keeping the attribution), then delete from_id.
        overrides = conn.execute(
            "update segment_person_overrides set person_id = ?, person_label = ? where person_id = ?",
            (payload.into_id, into_label, payload.from_id),
        )
        mappings = conn.execute(
            "update speaker_mappings set person_id = ? where person_id = ?",
            (payload.into_id, payload.from_id),
        )
        moved = overrides.rowcount + mappings.rowcount
        _delete_person(conn, person_id=payload.from_id)
        conn.commit()
    finally:
        conn.close()
    clear_projection_cache()  # the merged person's segments recolor -> any cached projection is stale
    return {"moved": moved}


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


@router.get("/speakers/embedding-status")
def embedding_status_route(request: Request, day: str | None = None, session_id: str | None = None) -> dict[str, int]:
    config: AppConfig = request.app.state.config
    where = ["ts.is_active = 1"]
    params: list[object] = []
    join = ""
    if session_id is not None:
        where.append("ts.session_id = ?")
        params.append(session_id)
    if day is not None:
        join = "join sessions s on s.session_id = ts.session_id"
        where.append("s.date_key = ?")
        params.append(day)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            f"""
            select
              count(*) as total,
              sum(case when exists (select 1 from segment_embeddings se where se.segment_id = ts.segment_id) then 1 else 0 end) as embedded
            from transcript_segments ts
            {join}
            where {" and ".join(where)}
            """,
            tuple(params),
        )
    finally:
        conn.close()
    total = int(rows[0]["total"] or 0)
    embedded = int(rows[0]["embedded"] or 0)
    return {"total": total, "embedded": embedded, "pending": total - embedded}


@router.get("/speakers/embedding-projection")
def embedding_projection_route(
    request: Request, session_id: str | None = None, day: str | None = None, method: str = "umap"
) -> dict[str, object]:
    """2D projection of in-scope CAM++ voiceprints for a scatter "voiceprint map" (UMAP/PCA)."""
    if method not in {"umap", "pca"}:
        raise HTTPException(status_code=400, detail="method must be one of: umap, pca")
    config: AppConfig = request.app.state.config
    return embedding_projection(config=config, session_id=session_id, day=day, method=method)


@router.post("/speakers/projection")
def projection_route(request: Request, payload: ProjectionRequest) -> dict[str, object]:
    """Multi-scope, tunable 2D projection of in-scope CAM++ voiceprints (UMAP/PCA-components/t-SNE).

    Projects the union of the given sessions + days together for cross-session comparison; over
    ``max_points`` segments are evenly subsampled to stay responsive.
    """
    if payload.method not in {"umap", "pca", "tsne"}:
        raise HTTPException(status_code=400, detail="method must be one of: umap, pca, tsne")
    config: AppConfig = request.app.state.config
    return project_embeddings(
        config=config,
        session_ids=payload.session_ids,
        days=payload.days,
        method=payload.method,
        n_neighbors=payload.n_neighbors,
        min_dist=payload.min_dist,
        pca_x=payload.pca_x,
        pca_y=payload.pca_y,
        perplexity=payload.perplexity,
        max_points=payload.max_points,
    )


@router.post("/speakers/extract-embeddings")
def extract_embeddings_route(request: Request, payload: ExtractEmbeddingsRequest) -> dict[str, bool]:
    """Kick off background CAM++ voiceprint extraction over pending segments (optionally scoped).

    The resident model is loaded and released inside the worker thread; returns immediately with
    started=False if an extraction (or any worker job) is already running.
    """
    worker = request.app.state.worker
    started = worker.start_embedding_extraction(session_id=payload.session_id, day=payload.day)
    return {"started": started}


@router.get("/emotions/status")
def emotion_status_route(request: Request, day: str | None = None, session_id: str | None = None) -> dict[str, int]:
    config: AppConfig = request.app.state.config
    where = ["ts.is_active = 1"]
    params: list[object] = []
    join = ""
    if session_id is not None:
        where.append("ts.session_id = ?")
        params.append(session_id)
    if day is not None:
        join = "join sessions s on s.session_id = ts.session_id"
        where.append("s.date_key = ?")
        params.append(day)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            f"""
            select
              count(*) as total,
              sum(case when exists (select 1 from segment_emotions se where se.segment_id = ts.segment_id) then 1 else 0 end) as emoted
            from transcript_segments ts
            {join}
            where {" and ".join(where)}
            """,
            tuple(params),
        )
    finally:
        conn.close()
    total = int(rows[0]["total"] or 0)
    emoted = int(rows[0]["emoted"] or 0)
    return {"total": total, "emoted": emoted, "pending": total - emoted}


@router.get("/emotions/distribution")
def emotion_distribution_route(
    request: Request, session_id: str | None = None, day: str | None = None
) -> dict[str, object]:
    """Per-segment dominant-emotion distribution (overall + per-speaker) over an active scope."""
    config: AppConfig = request.app.state.config
    return emotion_distribution(config=config, session_id=session_id, day=day)


@router.get("/emotions/labels")
def emotion_labels_route(
    request: Request, session_id: str | None = None, day: str | None = None
) -> dict[str, object]:
    """``{labels: {segment_id: dominant_emotion}}`` for the map's color-by-emotion mode."""
    config: AppConfig = request.app.state.config
    return {"labels": emotion_labels_for_scope(config=config, session_id=session_id, day=day)}


@router.post("/emotions/extract")
def extract_emotions_route(request: Request, payload: ExtractEmotionsRequest) -> dict[str, bool]:
    """Kick off background acoustic-emotion (emotion2vec) extraction over pending segments.

    The resident model is loaded and released inside the worker thread; returns immediately with
    started=False if an extraction (or any worker job) is already running.
    """
    worker = request.app.state.worker
    started = worker.start_emotion_extraction(session_id=payload.session_id, day=payload.day)
    return {"started": started}


@router.post("/speakers/recluster")
def recluster_route(request: Request, payload: ReclusterRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        return recluster_by_anchors(
            config=config,
            anchors=payload.anchors,
            threshold=payload.threshold,
            scope_session_id=payload.session_id,
            scope_day=payload.day,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/speakers/auto-cluster")
def auto_cluster_route(request: Request, payload: AutoClusterRequest) -> dict[str, object]:
    """Coarse unsupervised speaker grouping by voiceprint: rewrites speaker_cluster_id to global
    vp_* groups (replacing the per-file, collision-prone spk_NN labels). Run once for a first
    pass, then refine in the panel (rename / assign / merge). Reversible and re-runnable."""
    config: AppConfig = request.app.state.config
    return cluster_voiceprints(
        config=config,
        min_cluster_size=payload.min_cluster_size,
        scope_session_id=payload.session_id,
        scope_day=payload.day,
    )


@router.post("/speakers/mark-noise")
def mark_noise_route(request: Request, payload: MarkNoiseRequest) -> dict[str, object]:
    """Bulk-attribute meaningless segments to a non_speaker noise person: filler/backchannel text
    (嗯/啊/…) and/or segments shorter than a duration threshold. Never overwrites a manual label to
    a real person."""
    config: AppConfig = request.app.state.config
    try:
        return mark_noise_segments(
            config=config,
            noise_person_id=payload.noise_person_id,
            filler=payload.filler,
            max_duration_ms=payload.max_duration_ms,
            scope_session_id=payload.session_id,
            scope_day=payload.day,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/speakers/segments")
def segments_for_labeling_route(
    request: Request, session_id: str, speaker: str | None = None, limit: int = 200
) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    where = ["ts.is_active = 1", "ts.session_id = ?"]
    params: list[object] = [session_id]
    if speaker is not None:
        where.append("ts.speaker = ?")
        params.append(speaker)
    params.append(limit)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            f"""
            select
              ts.segment_id,
              ts.text,
              ts.speaker,
              ts.absolute_start_at,
              exists (select 1 from segment_embeddings se where se.segment_id = ts.segment_id) as has_embedding
            from transcript_segments ts
            where {" and ".join(where)}
            order by ts.absolute_start_at, ts.segment_id
            limit ?
            """,
            tuple(params),
        )
    finally:
        conn.close()
    segments = [
        {
            "segment_id": row["segment_id"],
            "text": row["text"],
            "speaker": row["speaker"],
            "absolute_start_at": row["absolute_start_at"],
            "has_embedding": bool(row["has_embedding"]),
        }
        for row in rows
    ]
    return {"segments": segments}


@router.post("/people/{person_id}/label-segments")
def label_segments_route(request: Request, person_id: str, payload: LabelSegmentsRequest) -> dict[str, int]:
    """Bulk-attribute segments to a person (the map's lasso-to-label commit)."""
    if not payload.segment_ids:
        raise HTTPException(status_code=400, detail="segment_ids must not be empty")
    config: AppConfig = request.app.state.config
    try:
        labeled = label_segments_as_person(config=config, person_id=person_id, segment_ids=payload.segment_ids)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"labeled": labeled}


@router.post("/people/{person_id}/enroll")
def enroll_person_route(request: Request, person_id: str, payload: EnrollPersonRequest) -> dict[str, object]:
    """Enroll a person's voiceprint from explicit segments (or their attributed segments)."""
    config: AppConfig = request.app.state.config
    conn = connect(config.database_path)
    try:
        initialize(conn)
        try:
            _person_label(conn, person_id=person_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        conn.close()
    try:
        return enroll_person(config=config, person_id=person_id, segment_ids=payload.segment_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/people")
def list_people_route(request: Request) -> dict[str, object]:
    """Persons enriched with enrollment + attribution counts (powers the People panel)."""
    config: AppConfig = request.app.state.config
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select
              p.person_id,
              p.display_name,
              p.person_type,
              p.is_self,
              exists (select 1 from person_voiceprints vp where vp.person_id = p.person_id) as enrolled,
              (select count(*) from segment_person_overrides o where o.person_id = p.person_id) as attributed_count,
              (select count(*) from segment_person_overrides o where o.person_id = p.person_id and o.source = 'manual') as manual_count
            from persons p
            order by p.is_self desc, p.display_name
            """,
        )
    finally:
        conn.close()
    people = [
        {
            "person_id": row["person_id"],
            "display_name": row["display_name"],
            "person_type": row["person_type"],
            "is_self": row["is_self"],
            "enrolled": bool(row["enrolled"]),
            "attributed_count": int(row["attributed_count"] or 0),
            "manual_count": int(row["manual_count"] or 0),
        }
        for row in rows
    ]
    return {"people": people}


@router.post("/speakers/suggest")
def suggest_people_route(request: Request, payload: SuggestPeopleRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return suggest_people_for_session(config=config, session_id=payload.session_id)


@router.post("/people/auto-attribute")
def auto_attribute_route(request: Request, payload: AutoAttributeRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        return auto_attribute_enrolled(
            config=config,
            session_id=payload.session_id,
            day=payload.day,
            threshold=payload.threshold,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
