from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

import numpy as np

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def put_embedding(*, config: AppConfig, segment_id: str, vector: Sequence[float], model: str = "cam++") -> None:
    """Store one voiceprint for a segment, upserting on segment_id."""
    array = np.asarray(vector, dtype=np.float32)
    if not np.all(np.isfinite(array)):
        raise ValueError("embedding vector must be finite (no NaN/inf)")
    now = _now()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into segment_embeddings (segment_id, model, dim, vector, created_at)
            values (?, ?, ?, ?, ?)
            on conflict(segment_id) do update set
              model = excluded.model, dim = excluded.dim,
              vector = excluded.vector, created_at = excluded.created_at
            """,
            (segment_id, model, len(array), array.tobytes(), now),
        )
        conn.commit()
    finally:
        conn.close()


def put_embeddings_bulk(*, config: AppConfig, items: list[tuple[str, Sequence[float]]], model: str = "cam++") -> int:
    """Upsert many voiceprints in one transaction; returns the count written."""
    if not items:
        return 0
    now = _now()
    rows = []
    for segment_id, vector in items:
        array = np.asarray(vector, dtype=np.float32)
        if not np.all(np.isfinite(array)):
            raise ValueError(f"embedding vector for {segment_id} must be finite (no NaN/inf)")
        rows.append((segment_id, model, len(array), array.tobytes(), now))
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.executemany(
            """
            insert into segment_embeddings (segment_id, model, dim, vector, created_at)
            values (?, ?, ?, ?, ?)
            on conflict(segment_id) do update set
              model = excluded.model, dim = excluded.dim,
              vector = excluded.vector, created_at = excluded.created_at
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


_SQL_CHUNK = 500  # keep IN/VALUES bind-var counts well under SQLite's per-statement limit


def get_embeddings(*, config: AppConfig, segment_ids: list[str]) -> dict[str, np.ndarray]:
    """Read back voiceprints as float32 ndarrays keyed by segment_id.

    The IN-clause is chunked so a large scope (a whole day/session — thousands of segments) never
    trips SQLite's per-statement bind-variable limit.
    """
    if not segment_ids:
        return {}
    result: dict[str, np.ndarray] = {}
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for start in range(0, len(segment_ids), _SQL_CHUNK):
            chunk = segment_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            rows = fetch_all(
                conn,
                f"select segment_id, dim, vector from segment_embeddings where segment_id in ({placeholders})",
                tuple(chunk),
            )
            for row in rows:
                array = np.frombuffer(row["vector"], dtype=np.float32)
                # frombuffer is read-only and shares the bytes buffer; copy to a standalone (dim,) array.
                result[str(row["segment_id"])] = array.reshape(int(row["dim"])).copy()
    finally:
        conn.close()
    return result


def pending_embedding_segment_ids(*, config: AppConfig, session_id: str | None = None, day: str | None = None) -> list[str]:
    """Active transcript segments lacking an embedding row, optionally scoped.

    Ordered by (absolute_start_at, segment_id) for deterministic batching.
    """
    where = [
        "ts.is_active = 1",
        "not exists (select 1 from segment_embeddings se where se.segment_id = ts.segment_id)",
    ]
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
            select ts.segment_id
            from transcript_segments ts
            {join}
            where {" and ".join(where)}
            order by ts.absolute_start_at, ts.segment_id
            """,
            tuple(params),
        )
    finally:
        conn.close()
    return [str(row["segment_id"]) for row in rows]


def scoped_embedding_segment_ids(*, config: AppConfig, session_id: str | None = None, day: str | None = None) -> list[str]:
    """Active transcript segments that DO have an embedding row, optionally scoped.

    Ordered by (absolute_start_at, segment_id) for deterministic processing.
    """
    where = [
        "ts.is_active = 1",
        "exists (select 1 from segment_embeddings se where se.segment_id = ts.segment_id)",
    ]
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
            select ts.segment_id
            from transcript_segments ts
            {join}
            where {" and ".join(where)}
            order by ts.absolute_start_at, ts.segment_id
            """,
            tuple(params),
        )
    finally:
        conn.close()
    return [str(row["segment_id"]) for row in rows]


def recluster_by_anchors(
    *,
    config: AppConfig,
    anchors: dict[str, str],
    threshold: float,
    scope_session_id: str | None = None,
    scope_day: str | None = None,
    model: str = "cam++",
) -> dict:
    """Attribute every in-scope segment to a person by voiceprint nearest-centroid.

    ``anchors`` maps ``segment_id -> person_id`` (a few labelled examples; multiple anchors per
    person allowed). Per-person centroids are the mean of the L2-normalized anchor vectors,
    re-normalized to unit length. Each in-scope segment is assigned to the person whose centroid
    it is most cosine-similar to, provided that best cosine is >= ``threshold``; otherwise it is
    left unassigned. Anchors are always assigned to their labelled person regardless of threshold.

    Writes ONLY segment_person_overrides (reusing the upsert helper) in a single transaction;
    never touches transcript_segments.speaker or speaker_cluster_id.
    """
    if not anchors:
        raise ValueError("anchors must be non-empty")
    if not (0.0 <= threshold <= 1.0):
        raise ValueError("threshold must be in [0, 1]")

    anchor_segment_ids = list(anchors.keys())
    anchor_embeddings = get_embeddings(config=config, segment_ids=anchor_segment_ids)
    if not anchor_embeddings:
        raise ValueError("no embeddings found for any anchor segment")
    # All anchors must share one dimensionality, else np.mean / the cosine matmul below would
    # raise an opaque error mid-pass (e.g. after a future re-embed with a different model build).
    anchor_dims = {int(v.shape[0]) for v in anchor_embeddings.values()}
    if len(anchor_dims) > 1:
        raise ValueError(f"inconsistent anchor embedding dim: {sorted(anchor_dims)}")

    # Build per-person centroids from L2-normalized anchor vectors.
    per_person_vectors: dict[str, list[np.ndarray]] = {}
    for segment_id, person_id in anchors.items():
        vector = anchor_embeddings.get(segment_id)
        if vector is None:
            continue
        per_person_vectors.setdefault(person_id, []).append(_normalize(vector))
    if not per_person_vectors:
        raise ValueError("no embeddings found for any anchor segment")

    person_ids: list[str] = list(per_person_vectors.keys())
    centroids = np.vstack([_normalize(np.mean(per_person_vectors[pid], axis=0)) for pid in person_ids])
    centroid_dim = int(centroids.shape[1])

    # All active in-scope segments that have an embedding.
    scope_ids = scoped_embedding_segment_ids(config=config, session_id=scope_session_id, day=scope_day)
    scope_embeddings = get_embeddings(config=config, segment_ids=scope_ids)

    # Anchors are forced to their labelled person even if they fall outside the scope query.
    candidate_ids = list(dict.fromkeys(scope_ids + anchor_segment_ids))

    now = _now()
    person_labels = _person_labels(config=config, person_ids=person_ids)

    assigned = 0
    per_person: dict[str, int] = {pid: 0 for pid in person_ids}
    writes: list[tuple[str, str]] = []  # (segment_id, person_id)

    for segment_id in candidate_ids:
        if segment_id in anchors:
            assigned_person = anchors[segment_id]
        else:
            vector = scope_embeddings.get(segment_id)
            # Skip a segment with no embedding, or one whose dim doesn't match the centroids
            # (a stray vector from a different model build would otherwise break the matmul).
            if vector is None or int(vector.shape[0]) != centroid_dim:
                continue
            sims = centroids @ _normalize(vector)
            best = int(np.argmax(sims))
            best_sim = float(sims[best])
            # Non-finite cosine (a corrupt NaN/inf embedding) is treated as below-threshold, NOT
            # silently force-assigned to person 0 — `nan < threshold` is False, so guard explicitly.
            if not np.isfinite(best_sim) or best_sim < threshold:
                continue
            assigned_person = person_ids[best]
        writes.append((segment_id, assigned_person))
        assigned += 1
        per_person[assigned_person] = per_person.get(assigned_person, 0) + 1

    total = len(candidate_ids)
    unassigned = total - assigned

    conn = connect(config.database_path)
    try:
        initialize(conn)
        from personal_context_node.speaker_review import upsert_segment_person_override

        for segment_id, person_id in writes:
            upsert_segment_person_override(
                conn,
                segment_id=segment_id,
                person_id=person_id,
                person_label=person_labels.get(person_id, person_id),
                now=now,
            )
        conn.commit()
    finally:
        conn.close()

    return {
        "assigned": assigned,
        "unassigned": unassigned,
        "total": total,
        "per_person": per_person,
        "threshold": threshold,
    }


def _normalize(vector: np.ndarray) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(array))
    if norm == 0.0:
        return array
    return array / norm


def _person_labels(*, config: AppConfig, person_ids: list[str]) -> dict[str, str]:
    if not person_ids:
        return {}
    placeholders = ", ".join("?" for _ in person_ids)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            f"select person_id, display_name from persons where person_id in ({placeholders})",
            tuple(person_ids),
        )
    finally:
        conn.close()
    return {str(row["person_id"]): str(row["display_name"]) for row in rows}


def extract_pending_embeddings(
    *,
    config: AppConfig,
    embed_fn: Callable[[str], list[float]],
    session_id: str | None = None,
    day: str | None = None,
    model: str = "cam++",
    batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Embed every pending transcript segment over its existing audio slice.

    ``embed_fn`` takes an audio file path (str) and returns the embedding vector; the heavy
    CAM++ model is injected here so this orchestration stays model-free (and test-stubbable).
    Segments whose audio slice is unavailable are skipped (kept pending) and counted separately.
    """
    # Lazy import: transcription.py pulls in heavier deps and may import this module transitively.
    from personal_context_node.transcription import segment_audio_path

    pending = pending_embedding_segment_ids(config=config, session_id=session_id, day=day)
    total = len(pending)
    embedded = 0
    skipped = 0
    failed = 0
    done = 0
    batch: list[tuple[str, Sequence[float]]] = []

    def flush() -> None:
        nonlocal embedded
        if batch:
            embedded += put_embeddings_bulk(config=config, items=batch, model=model)
            batch.clear()

    try:
        for segment_id in pending:
            path = segment_audio_path(config=config, segment_id=segment_id)
            if path is None:
                skipped += 1
            else:
                # One bad segment (corrupt slice, daemon error payload, decode failure) must NOT
                # abort the whole pass — the resident wrapper survives it, so count and continue.
                try:
                    vector = embed_fn(str(path))
                except Exception:
                    failed += 1
                else:
                    batch.append((segment_id, vector))
                    if len(batch) >= batch_size:
                        flush()
            done += 1
            if progress is not None:
                progress(done, total)
    finally:
        # Flush whatever was buffered even if an unexpected error escaped the loop.
        flush()
    return {"embedded": embedded, "skipped_missing_audio": skipped, "failed": failed, "total": total}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
