from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

import numpy as np

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def put_embedding(*, config: AppConfig, segment_id: str, vector: Sequence[float], model: str = "cam++") -> None:
    """Store one voiceprint for a segment, upserting on segment_id."""
    array = np.asarray(vector, dtype=np.float32)
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


def get_embeddings(*, config: AppConfig, segment_ids: list[str]) -> dict[str, np.ndarray]:
    """Read back voiceprints as float32 ndarrays keyed by segment_id."""
    if not segment_ids:
        return {}
    placeholders = ", ".join("?" for _ in segment_ids)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            f"select segment_id, dim, vector from segment_embeddings where segment_id in ({placeholders})",
            tuple(segment_ids),
        )
    finally:
        conn.close()
    result: dict[str, np.ndarray] = {}
    for row in rows:
        array = np.frombuffer(row["vector"], dtype=np.float32)
        # frombuffer is read-only and shares the bytes buffer; copy to a standalone (dim,) array.
        result[str(row["segment_id"])] = array.reshape(int(row["dim"])).copy()
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
