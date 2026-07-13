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


def _scope_query(
    *, session_id: str | None, day: str | None
) -> tuple[str, str, list[object]]:
    """Build the (join, where, params) fragments scoping active segments to a session/day."""
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
    return join, " and ".join(where), params


def emotion_distribution(
    *, config: AppConfig, session_id: str | None = None, day: str | None = None
) -> dict:
    """Aggregate per-segment dominant emotions over an active scope that HAS emotion rows.

    Joins transcript_segments to segment_emotions (inner — only segments with an emotion row
    count) and resolves each segment's speaker label = segment_person_overrides.person_label
    (if relabelled) else the raw speaker. Returns:
      - ``overall``: ``{emotion_label: count}`` across the scope by dominant label;
      - ``per_speaker``: ``[{label, total, emotions: {emotion: count}, dominant}]`` sorted by
        total desc (ties by label) — ``dominant`` is the speaker's most frequent emotion;
      - ``n``: total in-scope segments with an emotion.
    """
    join, where, params = _scope_query(session_id=session_id, day=day)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            f"""
            select
              coalesce(o.person_label, ts.speaker) as label,
              se.label as emotion
            from transcript_segments ts
            join segment_emotions se on se.segment_id = ts.segment_id
            left join segment_person_overrides o on o.segment_id = ts.segment_id
            {join}
            where {where}
            """,
            tuple(params),
        )
    finally:
        conn.close()

    overall: dict[str, int] = {}
    by_speaker: dict[str, dict[str, int]] = {}
    for row in rows:
        speaker = str(row["label"])
        emotion = str(row["emotion"])
        overall[emotion] = overall.get(emotion, 0) + 1
        emotions = by_speaker.setdefault(speaker, {})
        emotions[emotion] = emotions.get(emotion, 0) + 1

    per_speaker = []
    for speaker, emotions in by_speaker.items():
        total = sum(emotions.values())
        # Dominant = most frequent emotion (ties broken by label for determinism).
        dominant = max(emotions.items(), key=lambda kv: (kv[1], kv[0]))[0]
        per_speaker.append(
            {"label": speaker, "total": total, "emotions": emotions, "dominant": dominant}
        )
    per_speaker.sort(key=lambda s: (-int(s["total"]), str(s["label"])))

    return {"overall": overall, "per_speaker": per_speaker, "n": len(rows)}


def emotion_labels_for_scope(
    *, config: AppConfig, session_id: str | None = None, day: str | None = None
) -> dict[str, str]:
    """``{segment_id: dominant_emotion_label}`` for in-scope active segments with emotions.

    Powers the voiceprint map's color-by-emotion mode — every point that has an emotion row
    maps to its dominant class.
    """
    join, where, params = _scope_query(session_id=session_id, day=day)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            f"""
            select ts.segment_id as segment_id, se.label as emotion
            from transcript_segments ts
            join segment_emotions se on se.segment_id = ts.segment_id
            {join}
            where {where}
            """,
            tuple(params),
        )
    finally:
        conn.close()
    return {str(row["segment_id"]): str(row["emotion"]) for row in rows}


def pending_emotion_segment_ids(
    *,
    config: AppConfig,
    session_id: str | None = None,
    day: str | None = None,
    audio_file_id: str | None = None,
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


def _run_classify_batched(
    *,
    config: AppConfig,
    classify_batch_fn: Callable[[list[tuple[str, str]]], list[dict]],
    pending: list[str],
    model: str,
    batch_size: int,
    tick: Callable[[], None],
) -> dict:
    """Batched emotion sub-pass over ``pending``: bulk path resolve -> fixed-size chunks ->
    one ``classify_batch_fn`` wire round-trip per chunk -> bulk write per chunk.

    Unlike the embedding side there is NO padding constraint (the wrapper loops per item — the
    batch line only amortizes the wire round-trip), so chunks are simple size-capped slices in
    pending order. Per-item skip/fail semantics mirror the serial loop; a chunk whose wire call
    raises fails only that chunk. ``tick`` fires once per segment.
    """
    # Lazy import: transcription.py pulls in heavier deps and may import this module transitively.
    from personal_context_node.transcription import bulk_segment_audio_info

    info = bulk_segment_audio_info(config=config, segment_ids=pending)
    emoted = 0
    failed = 0
    skipped = 0
    items: list[tuple[str, str]] = []
    for segment_id in pending:
        entry = info.get(segment_id)
        if entry is None:
            skipped += 1
            tick()
        else:
            items.append((segment_id, str(entry[0])))

    size = max(1, int(batch_size))
    for start in range(0, len(items), size):
        chunk = items[start : start + size]
        try:
            results = classify_batch_fn(chunk)
        except Exception:
            failed += len(chunk)
            for _ in chunk:
                tick()
            continue
        writes: list[tuple[str, dict]] = []
        for (segment_id, _path), result in zip(chunk, results):
            if isinstance(result, dict) and "label" in result and "error" not in result:
                writes.append((segment_id, {"label": result["label"], "scores": result.get("scores", {})}))
            else:
                failed += 1
            tick()
        if writes:
            emoted += put_emotions_bulk(config=config, items=writes, model=model)
    return {"emoted": emoted, "skipped_missing_audio": skipped, "failed": failed, "total": len(pending)}


def extract_pending_emotions(
    *,
    config: AppConfig,
    classify_fn: Callable[[str], dict],
    classify_batch_fn: Callable[[list[tuple[str, str]]], list[dict]] | None = None,
    session_id: str | None = None,
    day: str | None = None,
    audio_file_id: str | None = None,
    model: str = _MODEL,
    batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Classify the acoustic emotion of every pending transcript segment over its audio slice.

    ``classify_fn`` takes an audio file path (str) and returns ``{"label", "scores"}``; the heavy
    emotion2vec model is injected here so this orchestration stays model-free (and test-stubbable).
    Segments whose audio slice is unavailable are skipped (kept pending) and counted separately.

    When ``classify_batch_fn`` is provided (see ``PersistentCommandEmotionAdapter.classify_batch``)
    the pass resolves audio paths in bulk and sends size-capped chunks over one wire round-trip
    each instead of one per segment. Omitted (the default), behavior is exactly the historical
    serial loop.
    """
    pending = pending_emotion_segment_ids(
        config=config, session_id=session_id, day=day, audio_file_id=audio_file_id
    )

    if classify_batch_fn is not None:
        total = len(pending)
        done = 0

        def _tick() -> None:
            nonlocal done
            done += 1
            if progress is not None:
                progress(done, total)

        return _run_classify_batched(
            config=config, classify_batch_fn=classify_batch_fn, pending=pending,
            model=model, batch_size=batch_size, tick=_tick,
        )

    # Lazy import: transcription.py pulls in heavier deps and may import this module transitively.
    from personal_context_node.transcription import segment_audio_path
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
