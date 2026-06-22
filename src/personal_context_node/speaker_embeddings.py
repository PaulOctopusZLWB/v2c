from __future__ import annotations

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

    clear_projection_cache()  # attributions changed -> any cached 2D projection is now stale
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
    clear_projection_cache()  # attributions changed -> any cached 2D projection is now stale
    # Re-enroll from the just-written manual labels so the person's voiceprint is current.
    try:
        enroll_person(config=config, person_id=person_id)
    except ValueError:
        # No embeddings yet for any labelled segment -> nothing to enroll; the labels still stand.
        pass
    return len(segment_ids)


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

    per_speaker_vectors: dict[str, list[np.ndarray]] = {}
    for segment_id in scope_ids:
        vector = embeddings.get(segment_id)
        speaker = speakers.get(segment_id)
        if vector is None or speaker is None or int(vector.shape[0]) != centroid_dim:
            continue
        per_speaker_vectors.setdefault(speaker, []).append(_normalize(vector))

    suggestions = []
    for speaker, vectors in per_speaker_vectors.items():
        cluster_mean = _normalize(np.mean(vectors, axis=0))
        sims = centroid_matrix @ cluster_mean
        best = int(np.argmax(sims))
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
    """
    if not (0.0 <= threshold <= 1.0):
        raise ValueError("threshold must be in [0, 1]")

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
    labeled_pairs = [(str(r["segment_id"]), str(r["person_id"])) for r in manual_rows]
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

    clear_projection_cache()  # attributions changed -> any cached 2D projection is now stale
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


# Projection results are deterministic for a given (database, scope, method, size), so cache them:
# a UMAP fit is a multi-second warmup and the scatter map is hit repeatedly as the UI re-renders.
_PROJECTION_CACHE: dict[tuple, dict] = {}

# The multi-scope, tunable projection (project_embeddings) keyed by its full parameter tuple.
# Kept separate from _PROJECTION_CACHE (different key shape) but cleared together so an attribution
# / embedding write invalidates BOTH the single-scope map and any cross-session projection.
_MULTI_PROJECTION_CACHE: dict[tuple, dict] = {}


def clear_projection_cache() -> None:
    """Drop all memoized projection results (call between tests / after re-embedding a scope)."""
    _PROJECTION_CACHE.clear()
    _MULTI_PROJECTION_CACHE.clear()


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

    UMAP (cosine metric, fixed seed) gives the best cluster separation but needs >=5 points and a
    multi-second warmup; PCA (numpy SVD on centered, L2-normalized vectors) is the deterministic
    fallback for small/instant cases and on any UMAP import/runtime failure. Results are memoized.
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

    matrix = np.vstack([embeddings[sid] for sid in ids]).astype(np.float64)
    # L2-normalize rows so PCA/UMAP both see unit voiceprints (cosine geometry).
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    X = matrix / norms

    used_method = "pca"
    coords: np.ndarray | None = None
    if method == "umap" and len(X) >= 5:
        try:
            import umap  # type: ignore

            reducer = umap.UMAP(
                n_components=2,
                n_neighbors=min(15, len(X) - 1),
                min_dist=0.1,
                metric="cosine",
                random_state=42,
            )
            with _UMAP_LOCK:  # numba workqueue is not threadsafe — never fit two UMAPs at once
                coords = np.asarray(reducer.fit_transform(X), dtype=np.float64)
            used_method = "umap"
        except Exception:
            coords = None  # any import/runtime failure -> deterministic PCA fallback below
    if coords is None:
        coords = _pca_2d(X)
        used_method = "pca"

    # Normalize coords to [0, 1]^2 for a stable scatter viewport.
    lo = coords.min(axis=0)
    span = coords.max(axis=0) - lo
    norm_coords = (coords - lo) / (span + 1e-9)

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

    matrix = np.vstack([embeddings[sid] for sid in ids]).astype(np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    X = matrix / norms

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
                random_state=42,
            )
            with _UMAP_LOCK:  # numba workqueue is not threadsafe — never fit two at once
                coords = np.asarray(reducer.fit_transform(X), dtype=np.float64)
            used_method = "umap"
        except Exception:
            coords = None  # any import/runtime failure -> deterministic PCA fallback
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


def _pca_2d(X: np.ndarray) -> np.ndarray:
    """Deterministic top-2 principal-component coordinates via SVD on centered rows."""
    centered = X - X.mean(axis=0, keepdims=True)
    # full_matrices=False keeps this cheap for the wide (n x 192) voiceprint matrix.
    _u, _s, vt = np.linalg.svd(centered, full_matrices=False)
    components = vt[:2]  # may be (1, d) when n == 1
    coords = centered @ components.T
    if coords.shape[1] < 2:  # degenerate (single point / single component): pad the missing axis.
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
    if embedded:
        clear_projection_cache()  # new voiceprints -> any cached 2D projection is now stale
    return {"embedded": embedded, "skipped_missing_audio": skipped, "failed": failed, "total": total}


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
        matrix = np.stack([np.asarray(embs[i], dtype=np.float64) for i in ids])
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        X = matrix / norms  # L2-normalize -> euclidean distance is monotone in cosine
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
                    random_state=42,
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
