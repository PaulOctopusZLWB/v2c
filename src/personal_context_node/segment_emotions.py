from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize

_MODEL = "emotion2vec_plus_base"
_SQL_CHUNK = 500  # keep IN/VALUES bind-var counts well under SQLite's per-statement limit


def put_emotions_bulk(*, config: AppConfig, items: list[tuple[str, dict]], model: str = _MODEL) -> int:
    """Upsert many per-segment emotions in one transaction; returns the count written.

    Each item is ``(segment_id, {"label": str, "scores": {label: float, ...}})``. The scores
    dict is serialized to ``scores_json``.
    """
    if not items:
        return 0
    now = _now()
    rows = []
    for segment_id, emotion in items:
        label = str(emotion["label"])
        scores_json = json.dumps(emotion["scores"], ensure_ascii=False)
        rows.append((segment_id, model, label, scores_json, now))
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.executemany(
            """
            insert into segment_emotions (segment_id, model, label, scores_json, created_at)
            values (?, ?, ?, ?, ?)
            on conflict(segment_id) do update set
              model = excluded.model, label = excluded.label,
              scores_json = excluded.scores_json, created_at = excluded.created_at
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def get_emotions(*, config: AppConfig, segment_ids: list[str]) -> dict[str, dict]:
    """Read back per-segment emotions keyed by segment_id as ``{"label", "scores"}``.

    The IN-clause is chunked so a large scope (a whole day/session — thousands of segments) never
    trips SQLite's per-statement bind-variable limit.
    """
    if not segment_ids:
        return {}
    result: dict[str, dict] = {}
    conn = connect(config.database_path)
    try:
        initialize(conn)
        for start in range(0, len(segment_ids), _SQL_CHUNK):
            chunk = segment_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            rows = fetch_all(
                conn,
                f"select segment_id, label, scores_json from segment_emotions where segment_id in ({placeholders})",
                tuple(chunk),
            )
            for row in rows:
                result[str(row["segment_id"])] = {
                    "label": str(row["label"]),
                    "scores": json.loads(row["scores_json"]),
                }
    finally:
        conn.close()
    return result


def pending_emotion_segment_ids(
    *, config: AppConfig, session_id: str | None = None, day: str | None = None
) -> list[str]:
    """Active transcript segments lacking an emotion row, optionally scoped.

    Ordered by (absolute_start_at, segment_id) for deterministic batching.
    """
    where = [
        "ts.is_active = 1",
        "not exists (select 1 from segment_emotions se where se.segment_id = ts.segment_id)",
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


def extract_pending_emotions(
    *,
    config: AppConfig,
    classify_fn: Callable[[str], dict],
    session_id: str | None = None,
    day: str | None = None,
    model: str = _MODEL,
    batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Classify the acoustic emotion of every pending transcript segment over its audio slice.

    ``classify_fn`` takes an audio file path (str) and returns ``{"label", "scores"}``; the heavy
    emotion2vec model is injected here so this orchestration stays model-free (and test-stubbable).
    Segments whose audio slice is unavailable are skipped (kept pending) and counted separately.
    """
    # Lazy import: transcription.py pulls in heavier deps and may import this module transitively.
    from personal_context_node.transcription import segment_audio_path

    pending = pending_emotion_segment_ids(config=config, session_id=session_id, day=day)
    total = len(pending)
    emoted = 0
    skipped = 0
    failed = 0
    done = 0
    batch: list[tuple[str, dict]] = []

    def flush() -> None:
        nonlocal emoted
        if batch:
            emoted += put_emotions_bulk(config=config, items=batch, model=model)
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
                    emotion = classify_fn(str(path))
                except Exception:
                    failed += 1
                else:
                    batch.append((segment_id, emotion))
                    if len(batch) >= batch_size:
                        flush()
            done += 1
            if progress is not None:
                progress(done, total)
    finally:
        # Flush whatever was buffered even if an unexpected error escaped the loop.
        flush()
    return {"emoted": emoted, "skipped_missing_audio": skipped, "failed": failed, "total": total}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
