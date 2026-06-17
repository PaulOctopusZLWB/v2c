from __future__ import annotations

from datetime import datetime

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def _parse_ms(value: object) -> int | None:
    """Parse an ISO-8601 timestamp to integer epoch milliseconds; None if unparseable.

    Segments can fan in across audio files, so per-file start_ms is not comparable; the
    absolute wall-clock timestamp is the only cross-file-safe ordering / offset basis.
    """
    if not value:
        return None
    text = str(value)
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def session_dynamics(*, config: AppConfig, session_id: str) -> dict[str, object]:
    """Per-session conversation dynamics: talk-time share, turn-taking, timeline.

    Loads the session's active segments ordered by absolute_start_at, resolving each one's
    attribution label = segment_person_overrides.person_label (if any) else the raw speaker.
    Computes:
      - per-speaker talk_ms (sum end-start), segment_count, turns (maximal same-label runs
        in time order), avg_segment_ms, talk_share (= talk_ms / total, 3dp);
      - transitions: {from,to,count} over consecutive turns;
      - timeline: a compact list of merged turns {label, start_ms_rel, end_ms_rel,
        segment_ids} where start/end are ms relative to the session's earliest absolute start
        (cross-file safe). Speakers sorted by talk_ms desc.
    """
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select
              ts.segment_id,
              ts.start_ms,
              ts.end_ms,
              ts.absolute_start_at,
              coalesce(o.person_label, ts.speaker) as label
            from transcript_segments ts
            left join segment_person_overrides o on o.segment_id = ts.segment_id
            where ts.session_id = ? and ts.is_active = 1
            order by ts.absolute_start_at, ts.start_ms, ts.segment_id
            """,
            (session_id,),
        )
    finally:
        conn.close()

    empty: dict[str, object] = {
        "session_id": session_id,
        "total_ms": 0,
        "speakers": [],
        "transitions": [],
        "timeline": [],
    }
    if not rows:
        return empty

    # Earliest absolute start anchors the relative timeline; fall back to None-safe handling
    # for legacy (chunk-mode) rows that carry no absolute timestamp.
    abs_ms = [_parse_ms(r["absolute_start_at"]) for r in rows]
    base = min((m for m in abs_ms if m is not None), default=None)

    # --- per-speaker aggregates ---
    per: dict[str, dict[str, int]] = {}
    total_ms = 0
    for r in rows:
        label = str(r["label"])
        talk = max(0, int(r["end_ms"]) - int(r["start_ms"]))
        total_ms += talk
        agg = per.setdefault(label, {"talk_ms": 0, "segment_count": 0})
        agg["talk_ms"] += talk
        agg["segment_count"] += 1

    # --- turns: maximal runs of the same label in chronological order ---
    turns: list[dict[str, object]] = []
    for idx, r in enumerate(rows):
        label = str(r["label"])
        seg_start_rel = _rel_start(abs_ms[idx], base, int(r["start_ms"]))
        seg_end_rel = seg_start_rel + max(0, int(r["end_ms"]) - int(r["start_ms"]))
        if turns and turns[-1]["label"] == label:
            current = turns[-1]
            current["segment_ids"].append(str(r["segment_id"]))  # type: ignore[union-attr]
            current["end_ms_rel"] = max(int(current["end_ms_rel"]), seg_end_rel)
        else:
            turns.append(
                {
                    "label": label,
                    "start_ms_rel": seg_start_rel,
                    "end_ms_rel": seg_end_rel,
                    "segment_ids": [str(r["segment_id"])],
                }
            )

    turn_counts: dict[str, int] = {}
    for turn in turns:
        turn_counts[str(turn["label"])] = turn_counts.get(str(turn["label"]), 0) + 1

    # --- transitions over consecutive turns (turn-taking) ---
    transition_counts: dict[tuple[str, str], int] = {}
    for prev_turn, next_turn in zip(turns, turns[1:]):
        key = (str(prev_turn["label"]), str(next_turn["label"]))
        transition_counts[key] = transition_counts.get(key, 0) + 1
    transitions = [
        {"from": frm, "to": to, "count": count}
        for (frm, to), count in sorted(transition_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    speakers = [
        {
            "label": label,
            "talk_ms": agg["talk_ms"],
            "talk_share": round(agg["talk_ms"] / total_ms, 3) if total_ms else 0.0,
            "turns": turn_counts.get(label, 0),
            "segment_count": agg["segment_count"],
            "avg_segment_ms": round(agg["talk_ms"] / agg["segment_count"], 3) if agg["segment_count"] else 0.0,
        }
        for label, agg in per.items()
    ]
    speakers.sort(key=lambda s: (-int(s["talk_ms"]), str(s["label"])))

    return {
        "session_id": session_id,
        "total_ms": total_ms,
        "speakers": speakers,
        "transitions": transitions,
        "timeline": turns,
    }


def _rel_start(seg_abs_ms: int | None, base: int | None, seg_start_ms: int) -> int:
    """Relative ms offset of a segment from the session start.

    Prefer the absolute-timestamp delta (cross-file safe). For rows without an absolute
    timestamp, fall back to the per-file start_ms (best effort for legacy chunk-mode data).
    """
    if seg_abs_ms is not None and base is not None:
        return max(0, seg_abs_ms - base)
    return max(0, seg_start_ms)
