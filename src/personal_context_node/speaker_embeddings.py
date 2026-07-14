from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

import numpy as np

# UMAP runs on numba's workqueue threading layer, which is NOT threadsafe — two concurrent
# UMAP runs (the web server serves projection requests on multiple threads) abort the whole
# process ("Concurrent access has been detected"). Serialize every UMAP fit behind this lock.
_UMAP_LOCK = threading.Lock()

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
    # Payloads must refresh; coords are content-addressed and self-invalidate, and keeping them
    # means a background ingest can't scramble the layout of a scope whose vectors didn't change.
    clear_projection_results_cache()


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
    # Payloads must refresh; coords are content-addressed and self-invalidate, and keeping them
    # means a background ingest can't scramble the layout of a scope whose vectors didn't change.
    clear_projection_results_cache()
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


def pending_embedding_segment_ids(
    *,
    config: AppConfig,
    session_id: str | None = None,
    day: str | None = None,
    audio_file_id: str | None = None,
) -> list[str]:
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
    if audio_file_id is not None:
        where.append("ts.audio_file_id = ?")
        params.append(audio_file_id)
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

    clear_projection_results_cache()  # attributions recolor points; fitted coords stay valid
    return {
        "assigned": assigned,
        "unassigned": unassigned,
        "total": total,
        "per_person": per_person,
        "threshold": threshold,
    }


def label_segments_as_person(*, config: AppConfig, person_id: str, segment_ids: list[str]) -> int:
    """Bulk-attribute a list of segments to one person (the map's lasso-to-label primitive).

    Resolves the person's label from ``persons.display_name`` (raising ``ValueError`` if the
    person is unknown) and upserts a ``segment_person_overrides`` row for every segment in ONE
    transaction. These are GROUND TRUTH, so each row is written with ``source='manual'``. After the
    labels commit, the person's voiceprint is re-enrolled so the centroid immediately reflects the
    new labels. Empty input writes nothing and returns 0. Returns the count written.
    """
    if not segment_ids:
        return 0
    from personal_context_node.speaker_review import upsert_segment_person_override

    now = _now()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select display_name from persons where person_id = ?", (person_id,))
        if not rows:
            raise ValueError(f"unknown person_id: {person_id}")
        person_label = str(rows[0]["display_name"])
        for segment_id in segment_ids:
            upsert_segment_person_override(
                conn,
                segment_id=segment_id,
                person_id=person_id,
                person_label=person_label,
                now=now,
                source="manual",
            )
        conn.commit()
    finally:
        conn.close()
    clear_projection_results_cache()  # attributions recolor points; fitted coords stay valid
    # Re-enroll from the just-written manual labels so the person's voiceprint is current.
    try:
        enroll_person(config=config, person_id=person_id)
    except ValueError:
        # No embeddings yet for any labelled segment -> nothing to enroll; the labels still stand.
        pass
    return len(segment_ids)


def clear_segment_person_attributions(*, config: AppConfig, segment_ids: list[str]) -> dict[str, int]:
    """Clear explicit person attributions for selected segments.

    This is the map legend's "回到未识别" primitive: it deletes only per-segment overrides, leaving
    person rows, speaker mappings, transcript text, and voiceprint embeddings untouched.
    """
    if not segment_ids:
        return {"cleared": 0}

    cleared = 0
    unique_ids = list(dict.fromkeys(segment_ids))
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for start in range(0, len(unique_ids), _SQL_CHUNK):
            chunk = unique_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            cur = conn.execute(
                f"delete from segment_person_overrides where segment_id in ({placeholders})",
                tuple(chunk),
            )
            cleared += int(cur.rowcount)
        conn.commit()
    finally:
        conn.close()

    if cleared:
        clear_projection_results_cache()  # attributions recolor points; fitted coords stay valid
    return {"cleared": cleared}


def preview_neighbor_corrections(
    *,
    config: AppConfig,
    session_ids: list[str] | None = None,
    days: list[str] | None = None,
    k: int = 15,
    min_neighbours: int = 8,
    majority_ratio: float = 0.75,
    similarity_floor: float = 0.35,
    max_points: int = 4000,
) -> dict:
    """Preview local-neighbour person attribution corrections for the current map scope.

    This is a conservative smoothing pass for "one wrong colour inside a strong cluster". It reads
    current overrides, votes over nearest in-scope embedding neighbours, and returns a dry-run plan.
    Manual overrides can vote as neighbours but are never mutation candidates.
    """
    params = _neighbor_params(k=k, min_neighbours=min_neighbours, majority_ratio=majority_ratio, similarity_floor=similarity_floor, max_points=max_points)
    scope_ids, total_before_cap = _correction_scope_segment_ids(
        config=config,
        session_ids=session_ids,
        days=days,
        max_points=params["max_points"],
    )
    if not scope_ids:
        return _empty_neighbor_preview(total=0, total_before_cap=0, params=params)

    embeddings = get_embeddings(config=config, segment_ids=scope_ids)
    usable_ids, matrix = _normalized_scope_matrix(scope_ids=scope_ids, embeddings=embeddings)
    if not usable_ids:
        raise ValueError("no usable embeddings in correction scope")

    attributions = _segment_override_attributions(config=config, segment_ids=usable_ids)
    skipped_manual = sum(1 for sid in usable_ids if attributions.get(sid, {}).get("source") == "manual")
    corrections: list[dict[str, object]] = []

    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -np.inf)
    k_eff = min(params["k"], max(0, len(usable_ids) - 1))
    if k_eff == 0:
        return _empty_neighbor_preview(total=len(usable_ids), total_before_cap=total_before_cap, params=params, skipped_manual=skipped_manual)

    for row_idx, segment_id in enumerate(usable_ids):
        current = attributions.get(segment_id, {})
        if current.get("source") == "manual":
            continue
        current_person = current.get("person_id")
        top_idx = np.argsort(-sims[row_idx])[:k_eff]
        neighbours: list[tuple[str | None, float]] = []
        for idx in top_idx:
            sim = float(sims[row_idx, idx])
            if not np.isfinite(sim) or sim < params["similarity_floor"]:
                continue
            neighbour_attr = attributions.get(usable_ids[int(idx)], {})
            neighbours.append((neighbour_attr.get("person_id"), sim))
        if len(neighbours) < params["min_neighbours"]:
            continue

        vote_people = [person_id for person_id, _sim in neighbours if person_id is not None]
        if current_person is not None and len(vote_people) < len(neighbours):
            vote_people.extend([None] * (len(neighbours) - len(vote_people)))
        if len(vote_people) < params["min_neighbours"]:
            continue

        counts: dict[str | None, int] = {}
        for person_id in vote_people:
            counts[person_id] = counts.get(person_id, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], "" if item[0] is None else str(item[0])))
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            continue
        target_person, winning_count = ranked[0]
        confidence = winning_count / float(len(vote_people))
        if confidence < params["majority_ratio"] or target_person == current_person:
            continue

        target_label = _attribution_label(attributions=attributions, person_id=target_person)
        corrections.append(
            {
                "segment_id": segment_id,
                "from_person_id": current_person,
                "from_person_label": current.get("person_label") if current_person is not None else "未识别",
                "to_person_id": target_person,
                "to_person_label": target_label,
                "neighbor_count": len(vote_people),
                "majority_count": winning_count,
                "confidence": round(confidence, 4),
            }
        )

    return {
        "total": len(usable_ids),
        "total_before_cap": total_before_cap,
        "changed": len(corrections),
        "skipped_manual": skipped_manual,
        "groups": _neighbor_correction_groups(corrections),
        "corrections": corrections,
        "params": params,
    }


def apply_neighbor_corrections(
    *,
    config: AppConfig,
    session_ids: list[str] | None = None,
    days: list[str] | None = None,
    k: int = 15,
    min_neighbours: int = 8,
    majority_ratio: float = 0.75,
    similarity_floor: float = 0.35,
    max_points: int = 4000,
) -> dict:
    """Apply the previewed neighbour corrections as voiceprint-sourced overrides."""
    preview = preview_neighbor_corrections(
        config=config,
        session_ids=session_ids,
        days=days,
        k=k,
        min_neighbours=min_neighbours,
        majority_ratio=majority_ratio,
        similarity_floor=similarity_floor,
        max_points=max_points,
    )
    corrections = list(preview.get("corrections", []))
    if not corrections:
        preview["applied"] = 0
        return preview

    now = _now()
    from personal_context_node.speaker_review import upsert_segment_person_override

    conn = connect(config.database_path)
    applied = 0
    try:
        initialize(conn)
        for correction in corrections:
            segment_id = str(correction["segment_id"])
            to_person_id = correction.get("to_person_id")
            if to_person_id is None:
                cur = conn.execute(
                    "delete from segment_person_overrides where segment_id = ? and source = 'voiceprint'",
                    (segment_id,),
                )
                applied += int(cur.rowcount)
                continue
            upsert_segment_person_override(
                conn,
                segment_id=segment_id,
                person_id=str(to_person_id),
                person_label=str(correction["to_person_label"]),
                now=now,
                source="voiceprint",
            )
            applied += 1
        conn.commit()
    finally:
        conn.close()

    if applied:
        clear_projection_results_cache()  # attributions recolor points; fitted coords stay valid
    preview["applied"] = applied
    return preview


def enroll_person(*, config: AppConfig, person_id: str, segment_ids: list[str] | None = None) -> dict:
    """Compute and persist a person's voiceprint centroid from their segments.

    If ``segment_ids`` is given those are used; otherwise the person's CONFIRMED labels are gathered
    from ``segment_person_overrides`` where ``source='manual'`` (NOT auto-inferred 'voiceprint'
    guesses — so an inferred attribution can never drift the centroid). The centroid is the
    re-normalized mean of the L2-normalized embedding vectors, stored as a float32 blob in
    ``person_voiceprints`` (upserted with n_segments + updated_at). Raises ``ValueError`` if no
    embeddings are found.

    Returns ``{"person_id", "n_segments", "dim"}``.
    """
    if segment_ids is None:
        conn = connect(config.database_path)
        try:
            initialize(conn)
            rows = fetch_all(
                conn,
                "select segment_id from segment_person_overrides where person_id = ? and source = 'manual'",
                (person_id,),
            )
        finally:
            conn.close()
        segment_ids = [str(row["segment_id"]) for row in rows]

    embeddings = get_embeddings(config=config, segment_ids=list(segment_ids))
    if not embeddings:
        raise ValueError(f"no embeddings found for person {person_id}")
    dims = {int(v.shape[0]) for v in embeddings.values()}
    if len(dims) > 1:
        raise ValueError(f"inconsistent embedding dim for person {person_id}: {sorted(dims)}")

    normalized = [_normalize(vector) for vector in embeddings.values()]
    centroid = _normalize(np.mean(normalized, axis=0)).astype(np.float32)
    dim = int(centroid.shape[0])
    n_segments = len(embeddings)
    now = _now()

    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into person_voiceprints (person_id, dim, vector, n_segments, updated_at)
            values (?, ?, ?, ?, ?)
            on conflict(person_id) do update set
              dim = excluded.dim, vector = excluded.vector,
              n_segments = excluded.n_segments, updated_at = excluded.updated_at
            """,
            (person_id, dim, centroid.tobytes(), n_segments, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"person_id": person_id, "n_segments": n_segments, "dim": dim}


def get_person_centroids(*, config: AppConfig) -> dict[str, np.ndarray]:
    """Read all enrolled voiceprints as unit-normalized float64 ndarrays keyed by person_id."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select person_id, dim, vector from person_voiceprints")
    finally:
        conn.close()
    result: dict[str, np.ndarray] = {}
    for row in rows:
        array = np.frombuffer(row["vector"], dtype=np.float32).reshape(int(row["dim"]))
        result[str(row["person_id"])] = _normalize(array.copy())
    return result


def suggest_people_for_session(*, config: AppConfig, session_id: str) -> dict:
    """Suggest the nearest enrolled person for each speaker cluster in a session.

    For each distinct ``speaker`` among the session's embedded segments, the cluster's mean
    (L2-normalized) embedding is matched by cosine to the nearest enrolled person centroid.
    Returns ``{"suggestions": [{"speaker", "person_id", "person_label", "score"}]}`` sorted by
    score desc. Clusters with no usable embedding are omitted; if no people are enrolled the
    suggestion list is empty.
    """
    centroids = get_person_centroids(config=config)
    # Never suggest a non_speaker (噪音/多人) person as a cluster's identity — it's a noise class,
    # not a real voiceprint identity, so drop its centroid from the suggestion candidates.
    non_speaker_ids = _non_speaker_person_ids(config=config)
    centroids = {pid: vec for pid, vec in centroids.items() if pid not in non_speaker_ids}
    if not centroids:
        return {"suggestions": []}
    person_ids = list(centroids.keys())
    centroid_matrix = np.vstack([centroids[pid] for pid in person_ids])
    centroid_dim = int(centroid_matrix.shape[1])
    person_labels = _person_labels(config=config, person_ids=person_ids)

    scope_ids = scoped_embedding_segment_ids(config=config, session_id=session_id)
    embeddings = get_embeddings(config=config, segment_ids=scope_ids)
    speakers = _segment_speakers(config=config, segment_ids=scope_ids)
    negative = _negative_person_ids_by_segment(config=config, segment_ids=scope_ids)

    per_speaker_vectors: dict[str, list[tuple[str, np.ndarray]]] = {}
    for segment_id in scope_ids:
        vector = embeddings.get(segment_id)
        speaker = speakers.get(segment_id)
        if vector is None or speaker is None or int(vector.shape[0]) != centroid_dim:
            continue
        per_speaker_vectors.setdefault(speaker, []).append((segment_id, _normalize(vector)))

    suggestions = []
    for speaker, segment_vectors in per_speaker_vectors.items():
        disallowed = set().union(*(negative.get(segment_id, set()) for segment_id, _ in segment_vectors))
        allowed_indices = [index for index, person_id in enumerate(person_ids) if person_id not in disallowed]
        if not allowed_indices:
            continue
        cluster_mean = _normalize(np.mean([vector for _, vector in segment_vectors], axis=0))
        sims = centroid_matrix @ cluster_mean
        best = allowed_indices[int(np.argmax(sims[allowed_indices]))]
        best_sim = float(sims[best])
        if not np.isfinite(best_sim):
            continue
        suggestions.append(
            {
                "speaker": speaker,
                "person_id": person_ids[best],
                "person_label": person_labels.get(person_ids[best], person_ids[best]),
                "score": round(best_sim, 3),
            }
        )
    suggestions.sort(key=lambda item: item["score"], reverse=True)
    return {"suggestions": suggestions}


_KNN_K = 15  # neighbours considered in the kNN vote (capped at the labeled-set size)
_KNN_COSINE_FLOOR = 0.25  # a far-away isolated point (best single cosine below this) stays unassigned


def auto_attribute_enrolled(
    *,
    config: AppConfig,
    session_id: str | None = None,
    day: str | None = None,
    threshold: float = 0.5,
    exclude_person_ids: set[str] | None = None,
) -> dict:
    """Global, manual-respecting "identify": assign every in-scope embedded segment to a person.

    Unlike a single per-person centroid (which is mediocre on multi-modal voices and ignores the
    local cluster structure the UMAP map shows), this is a **kNN over labelled segments**: every
    ``source='manual'`` override is a labelled exemplar, and each unlabelled in-scope segment is
    classified by a similarity-weighted vote of its K nearest labelled exemplars. This respects
    multi-modal voices (a person with two distinct vocal modes is two clusters of exemplars) and
    lets a ``non_speaker`` person act as a real "noise" class.

    Steps:
      a. Build the LABELLED set from every ``source='manual'`` override -> a matrix ``L`` of
         L2-normalized exemplar rows with a parallel ``labeled_person`` list. Raise ``ValueError``
         if there are no labelled segments.
      b. In scope, DELETE prior ``source='voiceprint'`` attributions (idempotent re-runs never
         accumulate stale guesses); ``source='manual'`` rows are NEVER touched.
      c. For each in-scope embedded segment that is NOT manually labelled, cosine to every labelled
         exemplar (one batched ``S @ L.T`` matmul), take the top-K nearest, and vote by their
         person_id. The winner's confidence = (sum of the winner's cosines among the top-K) /
         (sum of all top-K cosines) in [0, 1]. Assign the winner if confidence >= ``threshold`` AND
         the winner's best single cosine >= the cosine floor; else leave unassigned. Assigned rows
         are written ``source='voiceprint'``. Non-finite / dim-mismatched vectors are skipped.
      d. Returns ``{"assigned", "unassigned", "total", "per_person", "threshold"}`` where per_person
         counts only the voiceprint assignments (manual labels are separate).

    ``exclude_person_ids`` removes those persons' exemplars from the vote entirely (used by the
    identity-review cascade: a person marked "本场没出现" must not attract this scope's segments).
    """
    if not (0.0 <= threshold <= 1.0):
        raise ValueError("threshold must be in [0, 1]")
    excluded = exclude_person_ids or set()

    # (a) Build the labelled exemplar set from every manual override.
    conn = connect(config.database_path)
    try:
        initialize(conn)
        manual_rows = fetch_all(
            conn,
            "select segment_id, person_id from segment_person_overrides "
            "where source = 'manual' and person_id is not null",
        )
    finally:
        conn.close()
    labeled_pairs = [
        (str(r["segment_id"]), str(r["person_id"])) for r in manual_rows if str(r["person_id"]) not in excluded
    ]
    if not labeled_pairs:
        raise ValueError("no labeled segments to attribute against")

    labeled_embeddings = get_embeddings(config=config, segment_ids=[sid for sid, _ in labeled_pairs])
    # Determine the labelled dimensionality from the exemplars themselves (skip any with no
    # embedding yet). All usable exemplars must agree on dim, else the matmul is ill-defined.
    labeled_dims = {int(v.shape[0]) for v in labeled_embeddings.values() if np.all(np.isfinite(v))}
    if not labeled_dims:
        raise ValueError("no labeled segments have a usable embedding")
    labeled_dim = max(labeled_dims, key=lambda d: sum(1 for v in labeled_embeddings.values() if int(v.shape[0]) == d))

    labeled_vectors: list[np.ndarray] = []
    labeled_person: list[str] = []
    for segment_id, person_id in labeled_pairs:
        vector = labeled_embeddings.get(segment_id)
        if vector is None or int(vector.shape[0]) != labeled_dim or not np.all(np.isfinite(vector)):
            continue
        labeled_vectors.append(_normalize(vector))
        labeled_person.append(person_id)
    if not labeled_vectors:
        raise ValueError("no labeled segments have a usable embedding")

    L = np.vstack(labeled_vectors)  # [num_labeled x dim], rows L2-normalized
    labeled_person_arr = np.asarray(labeled_person, dtype=object)
    num_labeled = L.shape[0]
    K = min(_KNN_K, num_labeled)

    person_ids = list(dict.fromkeys(labeled_person))  # stable unique person order
    person_labels = _person_labels(config=config, person_ids=person_ids)
    now = _now()

    scope_ids = scoped_embedding_segment_ids(config=config, session_id=session_id, day=day)
    embeddings = get_embeddings(config=config, segment_ids=scope_ids)
    negative = _negative_person_ids_by_segment(config=config, segment_ids=scope_ids)

    # Manually-labelled in-scope segments: ground truth, never overwritten.
    manual_segment_ids = _manual_override_segment_ids(config=config, segment_ids=scope_ids)

    # (c) Gather the unlabelled in-scope candidates whose vector is finite and dim-matched, then do
    # the kNN vote in one batched matmul (S @ L.T) rather than a Python per-pair loop.
    candidate_ids: list[str] = []
    candidate_rows: list[np.ndarray] = []
    for segment_id in scope_ids:
        if segment_id in manual_segment_ids:
            continue  # manual labels always win
        vector = embeddings.get(segment_id)
        if vector is None or int(vector.shape[0]) != labeled_dim or not np.all(np.isfinite(vector)):
            continue
        candidate_ids.append(segment_id)
        candidate_rows.append(_normalize(vector))

    assigned = 0
    per_person: dict[str, int] = {pid: 0 for pid in person_ids}
    writes: list[tuple[str, str]] = []  # (segment_id, person_id)

    if candidate_rows:
        S = np.vstack(candidate_rows)  # [N x dim], rows L2-normalized
        sims = S @ L.T  # [N x num_labeled] cosine similarities
        # Indices of the top-K labelled exemplars per row (unordered within the partition is fine —
        # we sum over them and pick the winning person, order-independent).
        if K < num_labeled:
            top_idx = np.argpartition(-sims, K - 1, axis=1)[:, :K]
        else:
            top_idx = np.tile(np.arange(num_labeled), (sims.shape[0], 1))

        for row in range(S.shape[0]):
            idx = top_idx[row]
            top_sims = sims[row, idx]
            top_people = labeled_person_arr[idx]
            # Similarity-weighted vote: sum cosines per person among the top-K.
            denom = float(top_sims.sum())
            if not np.isfinite(denom) or denom <= 0.0:
                continue
            best_person: str | None = None
            best_weight = -1.0
            for pid in set(top_people.tolist()):
                weight = float(top_sims[top_people == pid].sum())
                if weight > best_weight:
                    best_weight = weight
                    best_person = pid
            if best_person is None:
                continue
            confidence = best_weight / denom
            # The winner's best single cosine must clear the floor (far-away isolated points stay out).
            best_single = float(top_sims[top_people == best_person].max())
            if confidence < threshold or best_single < _KNN_COSINE_FLOOR:
                continue
            if best_person in negative.get(candidate_ids[row], set()):
                continue
            writes.append((candidate_ids[row], best_person))
            assigned += 1
            per_person[best_person] = per_person.get(best_person, 0) + 1

    total = len(scope_ids)
    unassigned = total - assigned

    conn = connect(config.database_path)
    try:
        initialize(conn)
        from personal_context_node.speaker_review import upsert_segment_person_override

        # (c) Clear prior inferred attributions in scope so a re-run is idempotent and never
        # accumulates stale guesses; NEVER delete source='manual'.
        if scope_ids:
            for start in range(0, len(scope_ids), _SQL_CHUNK):
                chunk = scope_ids[start : start + _SQL_CHUNK]
                placeholders = ", ".join("?" for _ in chunk)
                conn.execute(
                    f"delete from segment_person_overrides "
                    f"where source = 'voiceprint' and segment_id in ({placeholders})",
                    tuple(chunk),
                )

        for segment_id, person_id in writes:
            upsert_segment_person_override(
                conn,
                segment_id=segment_id,
                person_id=person_id,
                person_label=person_labels.get(person_id, person_id),
                now=now,
                source="voiceprint",
            )
        conn.commit()
    finally:
        conn.close()

    clear_projection_results_cache()  # attributions recolor points; fitted coords stay valid
    return {
        "assigned": assigned,
        "unassigned": unassigned,
        "total": total,
        "per_person": per_person,
        "threshold": threshold,
    }


def _manual_override_segment_ids(*, config: AppConfig, segment_ids: list[str]) -> set[str]:
    """Subset of ``segment_ids`` that carry a ``source='manual'`` override (chunked IN-clause)."""
    if not segment_ids:
        return set()
    result: set[str] = set()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for start in range(0, len(segment_ids), _SQL_CHUNK):
            chunk = segment_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            rows = fetch_all(
                conn,
                f"select segment_id from segment_person_overrides "
                f"where source = 'manual' and segment_id in ({placeholders})",
                tuple(chunk),
            )
            for row in rows:
                result.add(str(row["segment_id"]))
    finally:
        conn.close()
    return result


def _negative_person_ids_by_segment(*, config: AppConfig, segment_ids: list[str]) -> dict[str, set[str]]:
    if not segment_ids:
        return {}
    result: dict[str, set[str]] = {}

    conn = connect(config.database_path)
    try:
        initialize(conn)
        for start in range(0, len(segment_ids), _SQL_CHUNK):
            chunk = segment_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            rows = fetch_all(
                conn,
                f"""
                select segment_id, person_id
                from segment_identity_negative_feedback
                where segment_id in ({placeholders})
                """,
                tuple(chunk),
            )
            for row in rows:
                result.setdefault(str(row["segment_id"]), set()).add(str(row["person_id"]))
    finally:
        conn.close()
    return result


def _neighbor_params(*, k: int, min_neighbours: int, majority_ratio: float, similarity_floor: float, max_points: int) -> dict[str, int | float]:
    if k < 1:
        raise ValueError("k must be >= 1")
    if min_neighbours < 1:
        raise ValueError("min_neighbours must be >= 1")
    if not (0.0 < majority_ratio <= 1.0):
        raise ValueError("majority_ratio must be in (0, 1]")
    if not (-1.0 <= similarity_floor <= 1.0):
        raise ValueError("similarity_floor must be in [-1, 1]")
    if max_points < 0:
        raise ValueError("max_points must be >= 0")
    return {
        "k": int(k),
        "min_neighbours": int(min_neighbours),
        "majority_ratio": float(majority_ratio),
        "similarity_floor": float(similarity_floor),
        "max_points": int(max_points),
    }


def _correction_scope_segment_ids(
    *,
    config: AppConfig,
    session_ids: list[str] | None,
    days: list[str] | None,
    max_points: int,
) -> tuple[list[str], int]:
    scope_ids: list[str] = []
    seen: set[str] = set()
    requested_sessions = list(session_ids or [])
    requested_days = list(days or [])

    if not requested_sessions and not requested_days:
        requested = scoped_embedding_segment_ids(config=config)
        for sid in requested:
            if sid not in seen:
                seen.add(sid)
                scope_ids.append(sid)
    else:
        for session_id in requested_sessions:
            for sid in scoped_embedding_segment_ids(config=config, session_id=session_id):
                if sid not in seen:
                    seen.add(sid)
                    scope_ids.append(sid)
        for day in requested_days:
            for sid in scoped_embedding_segment_ids(config=config, day=day):
                if sid not in seen:
                    seen.add(sid)
                    scope_ids.append(sid)

    total_before_cap = len(scope_ids)
    if max_points > 0 and total_before_cap > max_points:
        stride = total_before_cap / float(max_points)
        scope_ids = [scope_ids[int(i * stride)] for i in range(max_points)]
    return scope_ids, total_before_cap


def _normalized_scope_matrix(*, scope_ids: list[str], embeddings: dict[str, np.ndarray]) -> tuple[list[str], np.ndarray]:
    finite_dims = [
        int(vector.shape[0])
        for vector in embeddings.values()
        if np.all(np.isfinite(vector))
    ]
    if not finite_dims:
        return [], np.zeros((0, 0), dtype=np.float64)
    dim = max(set(finite_dims), key=finite_dims.count)
    usable_ids: list[str] = []
    rows: list[np.ndarray] = []
    for segment_id in scope_ids:
        vector = embeddings.get(segment_id)
        if vector is None or int(vector.shape[0]) != dim or not np.all(np.isfinite(vector)):
            continue
        usable_ids.append(segment_id)
        rows.append(_normalize(vector))
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float64)
    return usable_ids, np.vstack(rows).astype(np.float64)


def _segment_override_attributions(*, config: AppConfig, segment_ids: list[str]) -> dict[str, dict[str, str | None]]:
    result: dict[str, dict[str, str | None]] = {sid: {"person_id": None, "person_label": None, "source": None} for sid in segment_ids}
    if not segment_ids:
        return result
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for start in range(0, len(segment_ids), _SQL_CHUNK):
            chunk = segment_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            rows = fetch_all(
                conn,
                f"select segment_id, person_id, person_label, source from segment_person_overrides where segment_id in ({placeholders})",
                tuple(chunk),
            )
            for row in rows:
                result[str(row["segment_id"])] = {
                    "person_id": row["person_id"],
                    "person_label": row["person_label"],
                    "source": row["source"],
                }
    finally:
        conn.close()
    return result


def _attribution_label(*, attributions: dict[str, dict[str, str | None]], person_id: str | None) -> str:
    if person_id is None:
        return "未识别"
    for attr in attributions.values():
        if attr.get("person_id") == person_id and attr.get("person_label"):
            return str(attr["person_label"])
    return person_id


def _neighbor_correction_groups(corrections: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[object, object], dict[str, object]] = {}
    for correction in corrections:
        key = (correction.get("from_person_id"), correction.get("to_person_id"))
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "from_person_id": correction.get("from_person_id"),
                "from_person_label": correction.get("from_person_label"),
                "to_person_id": correction.get("to_person_id"),
                "to_person_label": correction.get("to_person_label"),
                "count": 0,
                "segment_ids": [],
            }
            grouped[key] = entry
        entry["count"] = int(entry["count"]) + 1
        cast_ids = entry["segment_ids"]
        if isinstance(cast_ids, list):
            cast_ids.append(str(correction["segment_id"]))
    return list(grouped.values())


def _empty_neighbor_preview(
    *,
    total: int,
    total_before_cap: int,
    params: dict[str, int | float],
    skipped_manual: int = 0,
) -> dict[str, object]:
    return {
        "total": total,
        "total_before_cap": total_before_cap,
        "changed": 0,
        "skipped_manual": skipped_manual,
        "groups": [],
        "corrections": [],
        "params": params,
    }


def _segment_speakers(*, config: AppConfig, segment_ids: list[str]) -> dict[str, str]:
    """Per-segment ``speaker`` cluster id, chunked to stay under SQLite's bind-var limit."""
    if not segment_ids:
        return {}
    result: dict[str, str] = {}
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for start in range(0, len(segment_ids), _SQL_CHUNK):
            chunk = segment_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            rows = fetch_all(
                conn,
                f"select segment_id, speaker from transcript_segments where segment_id in ({placeholders})",
                tuple(chunk),
            )
            for row in rows:
                result[str(row["segment_id"])] = str(row["speaker"])
    finally:
        conn.close()
    return result


# Projection state is memoized at two levels:
#
#  - Result caches: the full JSON-ready payload (points + per-segment speaker/person metadata).
#    _PROJECTION_CACHE serves the single-scope map, _MULTI_PROJECTION_CACHE the multi-scope
#    tunable projection (different key shapes). Stale after ANY write that changes what the map
#    shows — attribution writes AND embedding writes.
#  - _COORDS_CACHE: the fitted, normalized 2D coordinates, CONTENT-ADDRESSED: the key is a
#    blake2b digest of the segment ids + the actual vector bytes + the reducer params that apply
#    to the chosen method. A vector change changes the key, so entries can never go stale and no
#    write path needs to clear this layer — which is what keeps a background ingest from
#    scrambling the (unseeded, parallel) UMAP layout a user is currently lasso-labelling.
#    Bounded FIFO; guarded by _COORDS_LOCK (FastAPI serves projections on multiple threads).
_PROJECTION_CACHE: dict[tuple, dict] = {}
_MULTI_PROJECTION_CACHE: dict[tuple, dict] = {}
_COORDS_CACHE: dict[tuple, tuple[np.ndarray, str]] = {}
_COORDS_CACHE_MAX = 64  # FIFO bound; one entry is ~64 KB at the default 4k-point cap
_COORDS_LOCK = threading.Lock()  # guards _COORDS_CACHE get/store/evict/clear


def clear_projection_cache() -> None:
    """Drop ALL memoized projection state, coordinates included (full reset, e.g. between tests).

    Coordinate entries are content-addressed and can't go stale, so dropping them is about
    memory/test hygiene, not correctness.
    """
    _PROJECTION_CACHE.clear()
    _MULTI_PROJECTION_CACHE.clear()
    with _COORDS_LOCK:
        _COORDS_CACHE.clear()


def clear_projection_results_cache() -> None:
    """Drop memoized projection payloads but KEEP fitted 2D coordinates.

    For any write that changes what the map shows (attribution writes AND embedding writes):
    payloads must refresh, but coordinates are content-addressed — a scope whose vectors are
    unchanged keeps its layout, a changed scope simply misses the cache and refits.
    """
    _PROJECTION_CACHE.clear()
    _MULTI_PROJECTION_CACHE.clear()


def _umap_speed_kwargs(n_points: int) -> dict:
    """Shared UMAP performance tuning for the map projection and the pre-HDBSCAN reduction.

    Below 4096 points umap silently switches to an exact pairwise-kNN path that is ~10x slower
    than its NN-descent default here; force NN-descent once n is large enough for the
    approximation to be both safe and worthwhile. 500 epochs (umap's small-data default) refines
    little over 200 (its large-data default) on voiceprint clusters but costs 2.5x the layout time.
    """
    return {
        "force_approximation_algorithm": n_points >= 1024,
        "n_epochs": 500 if n_points < 1024 else 200,
    }


def _unit_matrix(ids: list[str], embeddings: dict[str, np.ndarray]) -> np.ndarray:
    """Stack the vectors for ``ids`` into an L2-row-normalized float32 matrix (cosine geometry).

    float32 on purpose: umap works in float32 internally, float64 input just forces an extra copy.
    """
    matrix = np.vstack([embeddings[sid] for sid in ids]).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


def _fit_coords_2d(
    X: np.ndarray,
    *,
    db_path: str,
    ids: list[str],
    method: str,
    n_neighbors: int = 15,
    min_dist: float = 0.1,
    perplexity: int = 30,
    pca_x: int = 0,
    pca_y: int = 1,
) -> tuple[np.ndarray, str]:
    """Reduce unit-normalized voiceprints to ``[0, 1]^2`` coords, memoized on content + params.

    UMAP runs UNSEEDED: a fixed ``random_state`` forces numba down to one thread (measured 6-35x
    slower on real voiceprints). Layout stability across re-renders and any write that leaves the
    scope's vectors unchanged comes from ``_COORDS_CACHE`` instead. PCA stays seed-free and
    deterministic by construction; t-SNE keeps its fixed seed (sklearn's Barnes-Hut gains nothing
    from threads).
    """
    # Content-addressed key: ids + vector bytes + only the params the chosen method actually
    # reads (so e.g. nudging the tsne-only perplexity slider while in umap mode is a cache hit,
    # not a refit that scrambles the layout). blake2b makes cross-scope collisions a non-issue.
    if method == "umap":
        params: tuple = ("umap", n_neighbors, float(min_dist))
    elif method == "tsne":
        params = ("tsne", perplexity)
    else:
        params = ("pca", pca_x, pca_y)
    digest = hashlib.blake2b("\x00".join(ids).encode(), digest_size=16)
    digest.update(np.ascontiguousarray(X).tobytes())
    cache_key = (db_path, digest.hexdigest(), params)
    with _COORDS_LOCK:
        cached = _COORDS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    used_method = "pca"
    coords: np.ndarray | None = None
    if method == "umap" and len(X) >= 5:
        try:
            import umap  # type: ignore

            reducer = umap.UMAP(
                n_components=2,
                n_neighbors=min(max(2, n_neighbors), len(X) - 1),
                min_dist=float(min_dist),
                metric="cosine",
                **_umap_speed_kwargs(len(X)),
            )
            with _UMAP_LOCK:  # numba workqueue is not threadsafe — never fit two at once
                coords = np.asarray(reducer.fit_transform(X), dtype=np.float64)
            used_method = "umap"
        except Exception:
            coords = None  # any import/runtime failure -> deterministic PCA fallback below
    elif method == "tsne" and len(X) >= 10:
        try:
            from sklearn.decomposition import PCA
            from sklearn.manifold import TSNE

            # PCA-reduce first (standard t-SNE preconditioning) then embed.
            pre_dim = min(50, X.shape[1], len(X) - 1)
            with _UMAP_LOCK:  # serialize all heavy embeddings ops (numba/BLAS thread safety)
                reduced = PCA(n_components=pre_dim, random_state=42).fit_transform(X) if pre_dim >= 2 else X
                perp = min(max(5, perplexity), (len(X) - 1) // 3 or 5)
                tsne = TSNE(n_components=2, perplexity=perp, init="pca", random_state=42)
                coords = np.asarray(tsne.fit_transform(reduced), dtype=np.float64)
            used_method = "tsne"
        except Exception:
            coords = None  # any failure -> deterministic PCA fallback

    if coords is None:
        coords = _pca_components(X, pca_x=pca_x, pca_y=pca_y)
        used_method = "pca"

    # Normalize coords to [0, 1]^2 for a stable scatter viewport.
    lo = coords.min(axis=0)
    span = coords.max(axis=0) - lo
    norm_coords = (coords - lo) / (span + 1e-9)

    # Cache only when the requested reducer actually ran: a PCA fallback is ~free to recompute,
    # and caching it under the umap/tsne key would pin a transient failure for the process life.
    if used_method == method:
        with _COORDS_LOCK:
            if len(_COORDS_CACHE) >= _COORDS_CACHE_MAX:
                _COORDS_CACHE.pop(next(iter(_COORDS_CACHE)))
            _COORDS_CACHE[cache_key] = (norm_coords, used_method)
    return norm_coords, used_method


def warm_projection_engine() -> threading.Thread:
    """Pre-compile the UMAP/numba stack in a background daemon thread.

    A cold process pays ~2s of ``import umap`` plus several seconds of numba JIT on the first
    projection request; fitting two throwaway datasets — one under umap's 4096-point exact-kNN
    cutoff and one on the forced NN-descent path — compiles both code paths up front. Best-effort:
    any failure is swallowed (a real request would then fall back to PCA anyway).
    """

    def _warm() -> None:
        try:
            import umap  # type: ignore

            rng = np.random.default_rng(0)
            # Take the lock once per fit, not across both: a real projection request that
            # arrives mid-warmup can interleave instead of queueing behind the whole warmup.
            with _UMAP_LOCK:
                umap.UMAP(n_components=2, n_neighbors=5, metric="cosine").fit_transform(
                    rng.random((64, 8), dtype=np.float32)
                )
            with _UMAP_LOCK:
                umap.UMAP(
                    n_components=2,
                    n_neighbors=5,
                    metric="cosine",
                    force_approximation_algorithm=True,
                    n_epochs=200,
                ).fit_transform(rng.random((1100, 8), dtype=np.float32))
        except Exception:
            pass

    thread = threading.Thread(target=_warm, name="umap-warmup", daemon=True)
    thread.start()
    return thread


def embedding_projection(
    *,
    config: AppConfig,
    session_id: str | None = None,
    day: str | None = None,
    method: str = "umap",
) -> dict:
    """Project stored CAM++ voiceprints in a scope down to 2D points for a scatter "voiceprint map".

    Returns ``{"points": [...], "method": <"umap"|"pca">, "n": <count>}`` where each point carries
    its ``segment_id``, normalized ``x``/``y`` in ``[0, 1]``, ``speaker``, person attribution
    (``person_id``/``person_label``, null when unlabeled) and a truncated ``text`` preview.

    UMAP (cosine metric, unseeded/parallel — see ``_fit_coords_2d``) gives the best cluster
    separation but needs >=5 points; PCA (numpy SVD on centered, L2-normalized vectors) is the
    deterministic fallback for small/instant cases and on any UMAP import/runtime failure.
    Results are memoized (payloads per scope, fitted coordinates per vector set).
    """
    scope_ids = scoped_embedding_segment_ids(config=config, session_id=session_id, day=day)
    cache_key = (str(config.database_path), session_id, day, method, len(scope_ids))
    cached = _PROJECTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if not scope_ids:
        result = {"points": [], "method": method, "n": 0}
        _PROJECTION_CACHE[cache_key] = result
        return result

    embeddings = get_embeddings(config=config, segment_ids=scope_ids)
    # Keep only ids that actually have a vector, preserving the deterministic scope order.
    ids = [sid for sid in scope_ids if sid in embeddings]
    if not ids:
        result = {"points": [], "method": method, "n": 0}
        _PROJECTION_CACHE[cache_key] = result
        return result

    X = _unit_matrix(ids, embeddings)
    norm_coords, used_method = _fit_coords_2d(X, db_path=str(config.database_path), ids=ids, method=method)

    metadata = _projection_metadata(config=config, segment_ids=ids)
    points = []
    for sid, (x, y) in zip(ids, norm_coords):
        meta = metadata.get(sid, {})
        points.append(
            {
                "segment_id": sid,
                "x": round(float(x), 4),
                "y": round(float(y), 4),
                "speaker": meta.get("speaker"),
                "person_id": meta.get("person_id"),
                "person_label": meta.get("person_label"),
                "text": meta.get("text"),
            }
        )

    result = {"points": points, "method": used_method, "n": len(points)}
    _PROJECTION_CACHE[cache_key] = result
    return result


def project_embeddings(
    *,
    config: AppConfig,
    session_ids: list[str] | None = None,
    days: list[str] | None = None,
    method: str = "umap",
    n_neighbors: int = 15,
    min_dist: float = 0.1,
    pca_x: int = 0,
    pca_y: int = 1,
    perplexity: int = 30,
    max_points: int = 4000,
) -> dict:
    """Project stored CAM++ voiceprints across MULTIPLE sessions/days to 2D, tunable + responsive.

    The scope is the union (dedup, order-preserving) of active, embedded segments across every
    ``session_ids`` entry PLUS every ``days`` entry. Over ``max_points`` segments are evenly
    subsampled (deterministic stride) so UMAP/t-SNE stay responsive; ``capped``/``total_in_scope``
    report this. ``method`` is ``"pca"`` (deterministic, selectable components ``pca_x``/``pca_y``),
    ``"umap"`` (needs >=5 points; tunable ``n_neighbors``/``min_dist``) or ``"tsne"`` (needs >=10;
    tunable ``perplexity``); umap/tsne fall back to PCA below their minimum or on any failure. Each
    point carries its ``session_id`` so the UI can color/compare by session. Results are memoized.
    """
    session_ids = list(session_ids or [])
    days = list(days or [])

    # Union of active, embedded segment ids across all sessions + days, dedup (order-preserving).
    scope_ids: list[str] = []
    seen: set[str] = set()
    for session_id in session_ids:
        for sid in scoped_embedding_segment_ids(config=config, session_id=session_id):
            if sid not in seen:
                seen.add(sid)
                scope_ids.append(sid)
    for day in days:
        for sid in scoped_embedding_segment_ids(config=config, day=day):
            if sid not in seen:
                seen.add(sid)
                scope_ids.append(sid)

    total_in_scope = len(scope_ids)

    # Evenly subsample (deterministic stride) when the scope exceeds the cap, so heavy reducers
    # stay responsive and the result is consistent across methods/calls.
    capped = False
    if max_points > 0 and total_in_scope > max_points:
        stride = total_in_scope / float(max_points)
        scope_ids = [scope_ids[int(i * stride)] for i in range(max_points)]
        capped = True

    cache_key = (
        str(config.database_path),
        tuple(sorted(session_ids)),
        tuple(sorted(days)),
        method,
        n_neighbors,
        min_dist,
        pca_x,
        pca_y,
        perplexity,
        max_points,
        total_in_scope,
    )
    cached = _MULTI_PROJECTION_CACHE.get(cache_key)
    if cached is not None:
        return cached

    if not scope_ids:
        result = {"points": [], "method": method, "n": 0, "capped": False}
        _MULTI_PROJECTION_CACHE[cache_key] = result
        return result

    embeddings = get_embeddings(config=config, segment_ids=scope_ids)
    ids = [sid for sid in scope_ids if sid in embeddings]
    if not ids:
        result = {"points": [], "method": method, "n": 0, "capped": False}
        _MULTI_PROJECTION_CACHE[cache_key] = result
        return result

    X = _unit_matrix(ids, embeddings)
    norm_coords, used_method = _fit_coords_2d(
        X,
        db_path=str(config.database_path),
        ids=ids,
        method=method,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        perplexity=perplexity,
        pca_x=pca_x,
        pca_y=pca_y,
    )

    metadata = _projection_metadata(config=config, segment_ids=ids)
    points = []
    for sid, (x, y) in zip(ids, norm_coords):
        meta = metadata.get(sid, {})
        points.append(
            {
                "segment_id": sid,
                "x": round(float(x), 4),
                "y": round(float(y), 4),
                "speaker": meta.get("speaker"),
                "person_id": meta.get("person_id"),
                "person_label": meta.get("person_label"),
                "text": meta.get("text"),
                "session_id": meta.get("session_id"),
            }
        )

    result = {
        "points": points,
        "method": used_method,
        "n": len(points),
        "capped": capped,
        "total_in_scope": total_in_scope,
    }
    _MULTI_PROJECTION_CACHE[cache_key] = result
    return result


def _pca_components(X: np.ndarray, *, pca_x: int, pca_y: int) -> np.ndarray:
    """Two selectable principal-component coordinates (columns ``pca_x``, ``pca_y``) via SVD.

    Indices are clamped into the available component range; if both resolve to the same axis,
    ``pca_y`` is bumped to a distinct one so the two output axes never collapse onto each other.
    """
    centered = X - X.mean(axis=0, keepdims=True)
    _u, _s, vt = np.linalg.svd(centered, full_matrices=False)
    n_components = vt.shape[0]
    ax = max(0, min(int(pca_x), n_components - 1))
    ay = max(0, min(int(pca_y), n_components - 1))
    if ay == ax and n_components >= 2:
        ay = ax + 1 if ax + 1 < n_components else ax - 1
    components = vt[[ax, ay]] if n_components >= 2 else vt[[ax]]
    coords = centered @ components.T
    if coords.shape[1] < 2:  # degenerate (single component): pad the missing axis.
        coords = np.hstack([coords, np.zeros((coords.shape[0], 2 - coords.shape[1]))])
    return coords


def _projection_metadata(*, config: AppConfig, segment_ids: list[str]) -> dict[str, dict]:
    """Per-segment {speaker, text(truncated), person_id, person_label, session_id} in one chunked query."""
    result: dict[str, dict] = {}
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for start in range(0, len(segment_ids), _SQL_CHUNK):
            chunk = segment_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            rows = fetch_all(
                conn,
                f"""
                select
                  ts.segment_id,
                  ts.speaker,
                  ts.text,
                  ts.session_id,
                  override.person_id as person_id,
                  override.person_label as person_label
                from transcript_segments ts
                left join segment_person_overrides override on override.segment_id = ts.segment_id
                where ts.segment_id in ({placeholders})
                """,
                tuple(chunk),
            )
            for row in rows:
                text = row["text"]
                if text is not None and len(text) > 60:
                    text = text[:60]
                result[str(row["segment_id"])] = {
                    "speaker": row["speaker"],
                    "text": text,
                    "person_id": row["person_id"],
                    "person_label": row["person_label"],
                    "session_id": row["session_id"],
                }
    finally:
        conn.close()
    return result


def _normalize(vector: np.ndarray) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(array))
    if norm == 0.0:
        return array
    return array / norm


def ensure_person(*, config: AppConfig, display_name: str, person_type: str = "contact") -> str:
    """Return the existing person with ``display_name``, or create one and return its id.

    Idempotent: used so a canonical noise person (``person_type='non_speaker'``) can be created
    without duplicating it on repeated calls. If a person with that display name already exists its
    id is returned unchanged (the existing person_type is NOT overwritten).
    """
    from uuid import uuid4

    now = _now()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select person_id from persons where display_name = ?", (display_name,))
        if rows:
            return str(rows[0]["person_id"])
        person_id = f"per_{uuid4().hex}"
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) "
            "values (?, ?, ?, 0, ?, ?)",
            (person_id, display_name, person_type, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return person_id


def _non_speaker_person_ids(*, config: AppConfig) -> set[str]:
    """Ids of persons whose ``person_type='non_speaker'`` (噪音/多人 — not a real voiceprint identity)."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(conn, "select person_id from persons where person_type = 'non_speaker'")
    finally:
        conn.close()
    return {str(row["person_id"]) for row in rows}


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


# Batching guardrail: funasr zero-pads a variable-length batch at the fbank-FRAME level and
# CAM++ has no length masking, so ANY padding corrupts the voiceprint — measured on real
# production slices, even duration ratios <= 1.25 within a bucket degraded cosine(solo vs
# batched same audio) to as low as 0.71 (downstream identify thresholds sit near 0.5, and the
# tens of thousands of stored embeddings were all computed solo). The ONLY safe grouping is
# zero padding at all: segments whose fbank frame counts are exactly equal batch to results
# bit-identical to solo inference (measured cosine 1.000000), and on real data ~94% of segments
# share their frame count with at least one sibling, ~80% are in groups of 4+.
#
# Kaldi fbank (25ms window / 10ms shift, snip_edges) at 16kHz gives
#   frames = 1 + (duration_ms - 25) // 10        for duration_ms >= 25
# which is exact for the 48k/16k sources this pipeline ingests (16000 samples/s divides both).


def _fbank_frame_key(duration_ms: int) -> int:
    """Bucket key: the exact Kaldi fbank frame count for a clip of ``duration_ms``.

    Sub-25ms clips (shorter than one fbank window) key on their negative exact millisecond
    length instead — equal length still means an identical, padding-free batch, and the
    negative range can never collide with a real frame count (which is >= 1).
    """
    duration = int(duration_ms)
    if duration < 25:
        return -duration - 1
    return 1 + (duration - 25) // 10


def _bucket_by_duration(
    items: list[tuple[str, str, int]],
    *,
    max_batch_size: int = 32,
) -> list[list[tuple[str, str]]]:
    """Group ``(segment_id, audio_path, duration_ms)`` into ZERO-PADDING buckets.

    Only segments with the exact same fbank frame count (see ``_fbank_frame_key``) share a
    bucket, capped at ``max_batch_size``; everything else rides a smaller (possibly singleton)
    bucket, which is exactly solo inference. Buckets come out in ascending frame-count order,
    items in their incoming (pending) order within a bucket — fully deterministic.
    """
    if not items:
        return []
    groups: dict[int, list[tuple[str, str]]] = {}
    for segment_id, path, duration_ms in items:
        groups.setdefault(_fbank_frame_key(int(duration_ms)), []).append((segment_id, path))
    size = max(1, int(max_batch_size))
    buckets: list[list[tuple[str, str]]] = []
    for key in sorted(groups):
        group = groups[key]
        for start in range(0, len(group), size):
            buckets.append(group[start : start + size])
    return buckets


def _run_embed_batched(
    *,
    config: AppConfig,
    embed_batch_fn: Callable[[list[tuple[str, str]]], list[dict]],
    pending: list[str],
    model: str,
    batch_size: int,
    tick: Callable[[], None],
) -> dict:
    """Batched embedding sub-pass over ``pending``: bulk path resolve -> duration buckets ->
    one ``embed_batch_fn`` wire round-trip per bucket -> bulk write per bucket.

    Per-item skip/fail semantics mirror the serial loop: a segment with no resolvable audio is
    "skipped" (kept pending); a per-item error entry or non-finite vector is "failed"; a whole
    bucket whose wire call raises (timeout/protocol poisoning — the adapter already killed the
    server) fails ONLY that bucket and the pass continues. ``tick`` fires once per segment.
    """
    # Lazy import: transcription.py pulls in heavier deps and may import this module transitively.
    from personal_context_node.transcription import bulk_segment_audio_info

    info = bulk_segment_audio_info(config=config, segment_ids=pending)
    embedded = 0
    failed = 0
    skipped = 0
    items: list[tuple[str, str, int]] = []
    for segment_id in pending:
        entry = info.get(segment_id)
        if entry is None:
            skipped += 1
            tick()
        else:
            items.append((segment_id, str(entry[0]), int(entry[1])))

    for bucket in _bucket_by_duration(items, max_batch_size=batch_size):
        try:
            results = embed_batch_fn(bucket)
        except Exception:
            failed += len(bucket)
            for _ in bucket:
                tick()
            continue
        writes: list[tuple[str, Sequence[float]]] = []
        for (segment_id, _path), result in zip(bucket, results):
            vector = result.get("embedding") if isinstance(result, dict) else None
            if vector is None or not np.all(np.isfinite(np.asarray(vector, dtype=np.float32))):
                failed += 1
            else:
                writes.append((segment_id, vector))
            tick()
        if writes:
            embedded += put_embeddings_bulk(config=config, items=writes, model=model)
    return {"embedded": embedded, "skipped_missing_audio": skipped, "failed": failed, "total": len(pending)}


def extract_pending_embeddings(
    *,
    config: AppConfig,
    embed_fn: Callable[[str], list[float]],
    embed_batch_fn: Callable[[list[tuple[str, str]]], list[dict]] | None = None,
    session_id: str | None = None,
    day: str | None = None,
    audio_file_id: str | None = None,
    model: str = "cam++",
    batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Embed every pending transcript segment over its existing audio slice.

    ``embed_fn`` takes an audio file path (str) and returns the embedding vector; the heavy
    CAM++ model is injected here so this orchestration stays model-free (and test-stubbable).
    Segments whose audio slice is unavailable are skipped (kept pending) and counted separately.

    When ``embed_batch_fn`` is provided (see ``PersistentCommandEmbedAdapter.embed_batch``) the
    pass runs the duration-bucketed BATCHED path instead of the per-segment serial loop — same
    counters, same return shape, ~7x faster on real hardware. Omitted (the default), behavior is
    exactly the historical serial loop.
    """
    pending = pending_embedding_segment_ids(
        config=config, session_id=session_id, day=day, audio_file_id=audio_file_id
    )

    if embed_batch_fn is not None:
        total = len(pending)
        done = 0

        def _tick() -> None:
            nonlocal done
            done += 1
            if progress is not None:
                progress(done, total)

        result = _run_embed_batched(
            config=config, embed_batch_fn=embed_batch_fn, pending=pending,
            model=model, batch_size=batch_size, tick=_tick,
        )
        if result["embedded"]:
            clear_projection_results_cache()  # new voiceprints -> payloads refresh; coords self-invalidate
        return result

    # Lazy import: transcription.py pulls in heavier deps and may import this module transitively.
    from personal_context_node.transcription import segment_audio_path
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
                    if not np.all(np.isfinite(np.asarray(vector, dtype=np.float32))):
                        failed += 1
                        continue
                    batch.append((segment_id, vector))
                    if len(batch) >= batch_size:
                        flush()
            done += 1
            if progress is not None:
                progress(done, total)
    finally:
        # Flush whatever was buffered even if an unexpected error escaped the loop.
        flush()
    if embedded:
        clear_projection_results_cache()  # new voiceprints -> payloads refresh; coords self-invalidate
    return {"embedded": embedded, "skipped_missing_audio": skipped, "failed": failed, "total": total}


def extract_pending_embeddings_and_emotions(
    *,
    config: AppConfig,
    embed_fn: Callable[[str], list[float]],
    classify_fn: Callable[[str], dict],
    embed_batch_fn: Callable[[list[tuple[str, str]]], list[dict]] | None = None,
    classify_batch_fn: Callable[[list[tuple[str, str]]], list[dict]] | None = None,
    session_id: str | None = None,
    day: str | None = None,
    audio_file_id: str | None = None,
    embed_model: str = "cam++",
    emotion_model: str | None = None,
    batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Embed AND classify-emotion every pending segment in one pass over its audio slice.

    Combines ``extract_pending_embeddings`` and ``segment_emotions.extract_pending_emotions``:
    the pending scope is the UNION of "missing embedding" and "missing emotion" segments, and each
    segment's audio path is resolved via ``segment_audio_path`` exactly ONCE regardless of how many
    of the two artifacts it is missing — then fed to whichever of ``embed_fn``/``classify_fn``
    applies. This halves the audio-path resolution and (when both are pending) the file I/O
    compared to running the two extractions back to back.

    Per artifact, the skip/fail/write semantics exactly mirror the standalone functions:
    - a segment with no resolvable audio path is "skipped" for whichever artifact it lacks (kept
      pending), counted in that artifact's ``skipped_missing_audio``;
    - one bad ``embed_fn``/``classify_fn`` call (or a non-finite embedding) is caught, counted as
      "failed" for that artifact ONLY, and does not affect the other artifact or abort the pass;
    - each artifact is upserted via its own bulk writer in batches of ``batch_size`` (independent
      batching per artifact, since one may have fewer pending than the other in this run).

    Returns ``{"embedding": {...as extract_pending_embeddings...}, "emotion": {...as
    extract_pending_emotions...}}``. ``progress(done, total)`` reports over the UNION size (one
    segment fully processed for whichever artifacts it needed = one "done" tick), so a caller
    driving a single progress bar for the combined pass sees an accurate total.

    When BOTH ``embed_batch_fn`` and ``classify_batch_fn`` are provided, the two artifacts run
    CONCURRENTLY on two threads (two resident model subprocesses working at once) with the
    batched per-artifact passes. Progress then ticks once per ARTIFACT operation — ``total`` is
    ``len(pending embeddings) + len(pending emotions)`` — still monotonic and ending at total,
    so any fraction-style progress consumer keeps working.
    """
    # Lazy import: transcription.py pulls in heavier deps and may import this module transitively.
    from personal_context_node.segment_emotions import (
        _MODEL as _EMOTION_MODEL,
        _run_classify_batched,
        pending_emotion_segment_ids,
        put_emotions_bulk,
    )
    from personal_context_node.transcription import segment_audio_path

    if emotion_model is None:
        emotion_model = _EMOTION_MODEL

    if embed_batch_fn is not None and classify_batch_fn is not None:
        pending_embed = pending_embedding_segment_ids(
            config=config, session_id=session_id, day=day, audio_file_id=audio_file_id
        )
        pending_emotion = pending_emotion_segment_ids(
            config=config, session_id=session_id, day=day, audio_file_id=audio_file_id
        )
        combined_total = len(pending_embed) + len(pending_emotion)
        tick_lock = threading.Lock()
        ticks = 0

        def _tick() -> None:
            nonlocal ticks
            with tick_lock:
                ticks += 1
                done_now = ticks
            if progress is not None:
                progress(done_now, combined_total)

        results: dict[str, dict] = {}
        errors: list[BaseException] = []

        def _embed_pass() -> None:
            try:
                results["embedding"] = _run_embed_batched(
                    config=config, embed_batch_fn=embed_batch_fn, pending=pending_embed,
                    model=embed_model, batch_size=batch_size, tick=_tick,
                )
            except BaseException as exc:  # surfaced after join — a thread must not die silently
                errors.append(exc)

        def _emotion_pass() -> None:
            try:
                results["emotion"] = _run_classify_batched(
                    config=config, classify_batch_fn=classify_batch_fn, pending=pending_emotion,
                    model=emotion_model, batch_size=batch_size, tick=_tick,
                )
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=_embed_pass, daemon=True),
            threading.Thread(target=_emotion_pass, daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise errors[0]
        if results.get("embedding", {}).get("embedded"):
            clear_projection_results_cache()  # new voiceprints -> payloads refresh; coords self-invalidate
        return {"embedding": results["embedding"], "emotion": results["emotion"]}

    pending_embed_ids = set(
        pending_embedding_segment_ids(config=config, session_id=session_id, day=day, audio_file_id=audio_file_id)
    )
    pending_emotion_ids = set(
        pending_emotion_segment_ids(config=config, session_id=session_id, day=day, audio_file_id=audio_file_id)
    )
    union_ids = sorted(pending_embed_ids | pending_emotion_ids)
    total = len(union_ids)

    embedded = 0
    embed_skipped = 0
    embed_failed = 0
    emoted = 0
    emotion_skipped = 0
    emotion_failed = 0
    done = 0

    embed_batch: list[tuple[str, Sequence[float]]] = []
    emotion_batch: list[tuple[str, dict]] = []

    def flush_embed() -> None:
        nonlocal embedded
        if embed_batch:
            embedded += put_embeddings_bulk(config=config, items=embed_batch, model=embed_model)
            embed_batch.clear()

    def flush_emotion() -> None:
        nonlocal emoted
        if emotion_batch:
            emoted += put_emotions_bulk(config=config, items=emotion_batch, model=emotion_model)
            emotion_batch.clear()

    try:
        for segment_id in union_ids:
            needs_embed = segment_id in pending_embed_ids
            needs_emotion = segment_id in pending_emotion_ids

            # Resolve the audio path exactly once, regardless of how many artifacts are pending.
            path = segment_audio_path(config=config, segment_id=segment_id)

            if needs_embed:
                if path is None:
                    embed_skipped += 1
                else:
                    try:
                        vector = embed_fn(str(path))
                    except Exception:
                        embed_failed += 1
                    else:
                        if not np.all(np.isfinite(np.asarray(vector, dtype=np.float32))):
                            embed_failed += 1
                        else:
                            embed_batch.append((segment_id, vector))
                            if len(embed_batch) >= batch_size:
                                flush_embed()

            if needs_emotion:
                if path is None:
                    emotion_skipped += 1
                else:
                    try:
                        emotion = classify_fn(str(path))
                    except Exception:
                        emotion_failed += 1
                    else:
                        emotion_batch.append((segment_id, emotion))
                        if len(emotion_batch) >= batch_size:
                            flush_emotion()

            done += 1
            if progress is not None:
                progress(done, total)
    finally:
        # Flush whatever was buffered even if an unexpected error escaped the loop.
        flush_embed()
        flush_emotion()

    if embedded:
        clear_projection_results_cache()  # new voiceprints -> payloads refresh; coords self-invalidate

    return {
        "embedding": {
            "embedded": embedded,
            "skipped_missing_audio": embed_skipped,
            "failed": embed_failed,
            "total": len(pending_embed_ids),
        },
        "emotion": {
            "emoted": emoted,
            "skipped_missing_audio": emotion_skipped,
            "failed": emotion_failed,
            "total": len(pending_emotion_ids),
        },
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cluster_voiceprints(
    *,
    config: AppConfig,
    min_cluster_size: int = 30,
    scope_session_id: str | None = None,
    scope_day: str | None = None,
    model: str = "cam++",
) -> dict:
    """Coarse unsupervised speaker grouping by voiceprint (HDBSCAN over CAM++ embeddings).

    Overwrites ``transcript_segments.speaker_cluster_id`` with GLOBAL voiceprint cluster ids
    ("vp_001", … by descending size) so the per-file, collision-prone diarize labels (spk_NN —
    the same string reused across files for different people) are replaced by cross-file groups.
    Assigning a vp_* cluster to a person then attributes that voice everywhere it occurs.

    - ``speaker = 'self'`` segments are left untouched (the owner stays auto-identified).
    - The original per-file label is preserved in ``transcript_segments.speaker``, so this is
      reversible (``update transcript_segments set speaker_cluster_id = speaker``).
    - Deterministic and re-runnable. HDBSCAN noise (-1) is bucketed as "vp_unassigned" for
      manual cleanup.

    Returns ``{"clusters", "assigned", "unassigned", "scope_segments"}``.
    """
    from collections import Counter

    from sklearn.cluster import HDBSCAN

    conn = connect(config.database_path)
    try:
        initialize(conn)
        where = [
            "ts.is_active = 1",
            "ts.speaker != 'self'",
            "exists (select 1 from segment_embeddings se where se.segment_id = ts.segment_id)",
        ]
        params: list[object] = []
        join = ""
        if scope_session_id is not None:
            where.append("ts.session_id = ?")
            params.append(scope_session_id)
        if scope_day is not None:
            join = "join sessions s on s.session_id = ts.session_id"
            where.append("s.date_key = ?")
            params.append(scope_day)
        rows = fetch_all(
            conn,
            f"select ts.segment_id from transcript_segments ts {join} where {' and '.join(where)} order by ts.segment_id",
            tuple(params),
        )
        ids = [str(r["segment_id"]) for r in rows]
        embs = get_embeddings(config=config, segment_ids=ids)
        ids = [i for i in ids if i in embs]
        if len(ids) < max(min_cluster_size, 2):
            return {"clusters": 0, "assigned": 0, "unassigned": 0, "scope_segments": len(ids)}
        X = _unit_matrix(ids, embs)  # unit rows -> euclidean distance is monotone in cosine
        # Reduce with UMAP before HDBSCAN: density clustering on the raw ~192-d voiceprints fails
        # (distances concentrate -> one giant cluster + mostly noise). UMAP (cosine) gives the
        # clean, density-friendly geometry the voiceprint map already shows. Raw fallback if UMAP
        # is unavailable or there are too few points.
        space = X
        if len(X) >= 5:
            try:
                import umap  # type: ignore

                reducer = umap.UMAP(
                    n_components=min(10, len(X) - 2),
                    n_neighbors=min(15, len(X) - 1),
                    min_dist=0.0,
                    metric="cosine",
                    # Seeded ON PURPOSE (unlike the map projection): cluster ids are persisted to
                    # transcript_segments, and this function promises deterministic re-runs. The
                    # seed costs parallelism, so claw the time back on the algorithm choice.
                    random_state=42,
                    **_umap_speed_kwargs(len(X)),
                )
                with _UMAP_LOCK:  # numba workqueue is not threadsafe — never fit two UMAPs at once
                    space = np.asarray(reducer.fit_transform(X), dtype=np.float64)
            except Exception:
                space = X
        labels = HDBSCAN(min_cluster_size=min_cluster_size).fit_predict(space)
        # Stable vp ids: largest cluster -> vp_001 (deterministic naming across re-runs).
        counts = Counter(int(label) for label in labels if label >= 0)
        vp_of = {label: f"vp_{rank + 1:03d}" for rank, (label, _) in enumerate(counts.most_common())}
        updates: list[tuple[str, str]] = []
        assigned = 0
        unassigned = 0
        for segment_id, label in zip(ids, labels):
            label = int(label)
            if label < 0:
                updates.append(("vp_unassigned", segment_id))
                unassigned += 1
            else:
                updates.append((vp_of[label], segment_id))
                assigned += 1
        conn.executemany(
            "update transcript_segments set speaker_cluster_id = ? where segment_id = ?",
            updates,
        )
        conn.commit()
        return {"clusters": len(vp_of), "assigned": assigned, "unassigned": unassigned, "scope_segments": len(ids)}
    finally:
        conn.close()


# Pure filler / backchannel interjections (no semantic content) — repetitions and punctuation
# only. Deliberately conservative: 对/行/不是/OK carry meaning and are NOT included.
_FILLER_RE = re.compile(r"^[嗯啊呃哦哎唉呣呵\s，。、？！!?,.~…\-]+$")


def mark_noise_segments(
    *,
    config: AppConfig,
    noise_person_id: str | None = None,
    filler: bool = False,
    max_duration_ms: int | None = None,
    scope_session_id: str | None = None,
    scope_day: str | None = None,
) -> dict:
    """Bulk-attribute meaningless segments to a non_speaker "noise" person (one-click cleanup).

    Targets segments matching EITHER criterion (union): ``filler`` = text is pure filler/backchannel
    ("嗯", "啊啊", …); ``max_duration_ms`` = shorter than the threshold. Segments already MANUALLY
    attributed to a different (real) person are left untouched, so this never clobbers ground truth.
    Writes ``segment_person_overrides`` (source='manual'); does NOT enroll a voiceprint (filler
    voiceprints are unreliable and must not drift any centroid).

    Returns ``{"marked", "noise_person_id", "noise_label", "scope_segments"}``.
    """
    if not filler and max_duration_ms is None:
        raise ValueError("specify filler=True and/or max_duration_ms")
    from personal_context_node.speaker_review import upsert_segment_person_override

    conn = connect(config.database_path)
    try:
        initialize(conn)
        if noise_person_id is None:
            rows = fetch_all(conn, "select person_id from persons where person_type = 'non_speaker' order by created_at limit 1")
            if not rows:
                raise ValueError("no non_speaker (noise) person exists; create one first")
            noise_person_id = str(rows[0]["person_id"])
        label_rows = fetch_all(conn, "select display_name from persons where person_id = ?", (noise_person_id,))
        if not label_rows:
            raise ValueError(f"unknown person_id: {noise_person_id}")
        noise_label = str(label_rows[0]["display_name"])

        where = ["ts.is_active = 1"]
        params: list[object] = []
        join = ""
        if scope_session_id is not None:
            where.append("ts.session_id = ?")
            params.append(scope_session_id)
        if scope_day is not None:
            join = "join sessions s on s.session_id = ts.session_id"
            where.append("s.date_key = ?")
            params.append(scope_day)
        # Never overwrite an existing MANUAL label to a different (real) person.
        where.append(
            "not exists (select 1 from segment_person_overrides o where o.segment_id = ts.segment_id "
            "and o.source = 'manual' and o.person_id != ?)"
        )
        params.append(noise_person_id)
        rows = fetch_all(
            conn,
            f"select ts.segment_id, ts.text, (ts.end_ms - ts.start_ms) as dur from transcript_segments ts {join} where {' and '.join(where)}",
            tuple(params),
        )
        scope_n = len(rows)
        now = _now()
        marked = 0
        for row in rows:
            dur = int(row["dur"])
            text = str(row["text"] or "")
            is_short = max_duration_ms is not None and dur < max_duration_ms
            is_filler = filler and bool(text.strip()) and _FILLER_RE.match(text.strip()) is not None
            if is_short or is_filler:
                upsert_segment_person_override(
                    conn, segment_id=str(row["segment_id"]), person_id=noise_person_id,
                    person_label=noise_label, now=now, source="manual",
                )
                marked += 1
        conn.commit()
        return {"marked": marked, "noise_person_id": noise_person_id, "noise_label": noise_label, "scope_segments": scope_n}
    finally:
        conn.close()


def global_clusters(*, config: AppConfig, min_size: int = 1) -> list[dict]:
    """List GLOBAL voiceprint clusters (vp_*) across ALL active segments, largest first.

    Each entry carries size, total speech ms, representative samples (longest segments), and the
    cluster's DOMINANT manual person attribution (if any) so the UI can show what is already
    assigned. Unlike the per-day /speakers/clusters, this aggregates cross-file vp_* groups, which
    is what the cluster→person panel assigns against.

    Returns ``[{cluster_id, segment_count, total_speech_ms, sample_segment_id, sample_text,
    sample_segments, person_id, person_label, labeled_count}]``.
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select ts.speaker_cluster_id as cluster_id,
                   count(*) as segment_count,
                   coalesce(sum(ts.end_ms - ts.start_ms), 0) as total_speech_ms,
                   (select s.segment_id from transcript_segments s
                      where s.speaker_cluster_id = ts.speaker_cluster_id and s.is_active = 1
                      order by (s.end_ms - s.start_ms) desc, s.segment_id limit 1) as sample_segment_id,
                   (select s.text from transcript_segments s
                      where s.speaker_cluster_id = ts.speaker_cluster_id and s.is_active = 1
                      order by (s.end_ms - s.start_ms) desc, s.segment_id limit 1) as sample_text
            from transcript_segments ts
            where ts.is_active = 1 and ts.speaker_cluster_id like 'vp_%'
            group by ts.speaker_cluster_id
            having count(*) >= ?
            order by segment_count desc, ts.speaker_cluster_id
            """,
            (min_size,),
        )
        dom = fetch_all(
            conn,
            """
            select cluster_id, person_id, person_label, c from (
              select ts.speaker_cluster_id as cluster_id, o.person_id, o.person_label, count(*) as c,
                     row_number() over (partition by ts.speaker_cluster_id order by count(*) desc) as rn
              from transcript_segments ts
              join segment_person_overrides o on o.segment_id = ts.segment_id and o.source = 'manual'
              where ts.is_active = 1 and ts.speaker_cluster_id like 'vp_%'
              group by ts.speaker_cluster_id, o.person_id, o.person_label
            ) where rn = 1
            """,
        )
        sample_rows = fetch_all(
            conn,
            """
            select cluster_id, segment_id, text from (
              select ts.speaker_cluster_id as cluster_id,
                     ts.segment_id,
                     ts.text,
                     row_number() over (
                       partition by ts.speaker_cluster_id
                       order by (ts.end_ms - ts.start_ms) desc, ts.segment_id
                     ) as rn
              from transcript_segments ts
              where ts.is_active = 1
                and ts.speaker_cluster_id like 'vp_%'
                and coalesce(trim(ts.text), '') != ''
            ) where rn <= 4
            order by cluster_id, rn
            """,
        )
        dom_by_cluster = {str(r["cluster_id"]): r for r in dom}
        samples_by_cluster: dict[str, list[dict[str, str]]] = {}
        for sample in sample_rows:
            cid = str(sample["cluster_id"])
            samples_by_cluster.setdefault(cid, []).append(
                {"segment_id": str(sample["segment_id"]), "text": str(sample["text"])}
            )
        out: list[dict] = []
        for row in rows:
            cid = str(row["cluster_id"])
            d = dom_by_cluster.get(cid)
            out.append(
                {
                    "speaker_cluster_id": cid,
                    "segment_count": int(row["segment_count"]),
                    "total_speech_ms": int(row["total_speech_ms"]),
                    "sample_segment_id": row["sample_segment_id"],
                    "sample_text": row["sample_text"],
                    "sample_segments": samples_by_cluster.get(cid, []),
                    "person_id": (d["person_id"] if d else None),
                    "person_label": (d["person_label"] if d else None),
                    "labeled_count": (int(d["c"]) if d else 0),
                }
            )
        return out
    finally:
        conn.close()


def assign_cluster_to_person(*, config: AppConfig, cluster_id: str, person_id: str) -> dict:
    """Attribute EVERY active segment of a voiceprint cluster to one person (one-click cluster→人).

    Writes per-segment manual overrides (via label_segments_as_person), so it is consistent with
    map-lasso labels, survives a re-cluster (overrides are per-segment, not keyed on the vp id), and
    re-enrolls the person's voiceprint from the enlarged manual set. Returns the labeled count.
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            "select segment_id from transcript_segments where speaker_cluster_id = ? and is_active = 1",
            (cluster_id,),
        )
        segment_ids = [str(r["segment_id"]) for r in rows]
    finally:
        conn.close()
    if not segment_ids:
        return {"cluster_id": cluster_id, "person_id": person_id, "labeled": 0}
    labeled = label_segments_as_person(config=config, person_id=person_id, segment_ids=segment_ids)
    return {"cluster_id": cluster_id, "person_id": person_id, "labeled": labeled}


def identification_status(*, config: AppConfig) -> dict:
    """Progress signals for the 声纹 tab's gate + stepper, over ALL active segments.

    - ``total`` active segments; ``embedded`` with a voiceprint; ``clusters`` distinct vp_* groups.
    - ``identified`` segments attributed to a person (override or cluster mapping, via
      v_segment_attribution); ``unidentified`` = total - identified. The day-level gate
      (require_identified_speakers) opens a day when its unidentified reaches 0; this global count
      is the overall "how close am I" signal.
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        total = int(fetch_all(conn, "select count(*) c from transcript_segments where is_active=1")[0]["c"])
        embedded = int(fetch_all(conn, "select count(*) c from transcript_segments ts where ts.is_active=1 and exists (select 1 from segment_embeddings se where se.segment_id=ts.segment_id)")[0]["c"])
        clusters = int(fetch_all(conn, "select count(distinct speaker_cluster_id) c from transcript_segments where is_active=1 and speaker_cluster_id like 'vp_%'")[0]["c"])
        identified = int(fetch_all(conn, "select count(*) c from transcript_segments ts join v_segment_attribution va on va.segment_id=ts.segment_id where ts.is_active=1 and va.person_id is not null")[0]["c"])
        return {
            "total": total,
            "embedded": embedded,
            "clusters": clusters,
            "identified": identified,
            "unidentified": max(0, total - identified),
        }
    finally:
        conn.close()
