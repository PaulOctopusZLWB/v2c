"""Automatic per-session speaker identification (the ``identify_speakers`` pipeline leaf).

Once voiceprints land for a session, this pass turns the raw segments into a reviewable
"who spoke" draft with zero clicks:

  1. **Match** — kNN-attribute every embedded segment against enrolled manual exemplars
     (``auto_attribute_enrolled``), excluding persons the identity review marked absent.
  2. **Prune** — a person whose voiceprint-sourced attributions cover less than
     ``config.identify_min_session_share`` of the session's embedded segments (and who has no
     manual label in the session) is almost always a spurious match; drop those attributions
     back to unassigned. ``non_speaker`` persons (the noise class) are exempt — noise is
     legitimately sparse.
  3. **Smooth** — one conservative neighbour-correction pass fixes "wrong colour inside a
     strong cluster" artifacts left by step 1 (guardrails inside apply_neighbor_corrections;
     manual labels are never touched).
  4. **Cluster leftovers** — session-scoped voiceprint clustering groups the remaining
     unattributed segments into vp_* candidates for the identity-review panel.

Like extract_features, this is a pure LEAF: it gates nothing downstream, so a failed or slow
identify can never delay sessions, summaries, or reports. Re-runs are idempotent (step 1 clears
prior voiceprint attributions in scope; manual overrides always survive every step).
"""

from __future__ import annotations

from personal_context_node.config import AppConfig
from personal_context_node.speaker_embeddings import (
    apply_neighbor_corrections,
    auto_attribute_enrolled,
    cluster_voiceprints,
    scoped_embedding_segment_ids,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize

_SQL_CHUNK = 500  # keep IN-clause bind-var counts well under SQLite's per-statement limit


def absent_person_ids(*, config: AppConfig, session_id: str) -> set[str]:
    """Persons the identity review marked ``absent`` for this session (excluded from matching)."""
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            "select person_id from session_participants where session_id = ? and status = 'absent'",
            (session_id,),
        )
        return {str(row["person_id"]) for row in rows}
    finally:
        conn.close()


def prune_low_share_attributions(
    *,
    config: AppConfig,
    session_id: str,
    min_share: float,
) -> dict:
    """Drop voiceprint-sourced attributions of persons below ``min_share`` of the session.

    "少于 min_share 会话有效位点的人": share = that person's voiceprint-attributed segments /
    the session's embedded active segments. Guards:
      - a person with ANY manual label in the session is never pruned (the reviewer vouched);
      - ``non_speaker`` persons are never pruned (the noise class is legitimately sparse);
      - only ``source='voiceprint'`` rows are deleted — manual ground truth survives.

    Returns ``{"pruned": {person_id: count}, "total_segments": n}``.
    """
    scope_ids = scoped_embedding_segment_ids(config=config, session_id=session_id)
    total = len(scope_ids)
    if total == 0 or min_share <= 0.0:
        return {"pruned": {}, "total_segments": total}

    conn = connect(config.database_path)
    try:
        initialize(conn)
        voiceprint_counts: dict[str, int] = {}
        manual_people: set[str] = set()
        for start in range(0, len(scope_ids), _SQL_CHUNK):
            chunk = scope_ids[start : start + _SQL_CHUNK]
            placeholders = ", ".join("?" for _ in chunk)
            rows = fetch_all(
                conn,
                f"select person_id, source from segment_person_overrides "
                f"where person_id is not null and segment_id in ({placeholders})",
                tuple(chunk),
            )
            for row in rows:
                person_id = str(row["person_id"])
                if row["source"] == "manual":
                    manual_people.add(person_id)
                elif row["source"] == "voiceprint":
                    voiceprint_counts[person_id] = voiceprint_counts.get(person_id, 0) + 1

        low_share = [
            person_id
            for person_id, count in voiceprint_counts.items()
            if count / float(total) < min_share and person_id not in manual_people
        ]
        if not low_share:
            return {"pruned": {}, "total_segments": total}

        # Noise-class persons are exempt: sparse by nature, and dropping their labels would
        # resurface filler segments as "unidentified" work in the review panel.
        placeholders = ", ".join("?" for _ in low_share)
        noise_rows = fetch_all(
            conn,
            f"select person_id from persons where person_id in ({placeholders}) and person_type = 'non_speaker'",
            tuple(low_share),
        )
        noise_ids = {str(row["person_id"]) for row in noise_rows}
        prune_ids = [person_id for person_id in low_share if person_id not in noise_ids]
        if not prune_ids:
            return {"pruned": {}, "total_segments": total}

        pruned: dict[str, int] = {}
        for person_id in prune_ids:
            deleted = 0
            for start in range(0, len(scope_ids), _SQL_CHUNK):
                chunk = scope_ids[start : start + _SQL_CHUNK]
                placeholders = ", ".join("?" for _ in chunk)
                cur = conn.execute(
                    f"delete from segment_person_overrides "
                    f"where source = 'voiceprint' and person_id = ? and segment_id in ({placeholders})",
                    (person_id, *chunk),
                )
                deleted += int(cur.rowcount)
            if deleted:
                pruned[person_id] = deleted
        conn.commit()
    finally:
        conn.close()

    if pruned:
        from personal_context_node.speaker_embeddings import clear_projection_results_cache

        clear_projection_results_cache()  # attributions recolor points; fitted coords stay valid
    return {"pruned": pruned, "total_segments": total}


def identify_session_speakers(*, config: AppConfig, session_id: str) -> dict:
    """Run the full automatic identify pass for one session (match → prune → smooth → cluster).

    Cold-start safe: with no enrolled exemplars yet, step 1 is skipped and the pass still
    clusters the session's voices into reviewable vp_* candidates. Absent participants from the
    identity review are excluded from matching, so a manual re-trigger after review converges
    instead of re-asserting rejected people.
    """
    absent = absent_person_ids(config=config, session_id=session_id)

    attributed: dict = {"assigned": 0, "unassigned": 0, "total": 0, "per_person": {}, "skipped": False}
    try:
        attributed = auto_attribute_enrolled(
            config=config,
            session_id=session_id,
            threshold=config.identify_threshold,
            exclude_person_ids=absent,
        )
    except ValueError:
        # No labelled exemplars anywhere yet (fresh database): nothing to match against.
        attributed["skipped"] = True

    pruned = prune_low_share_attributions(
        config=config,
        session_id=session_id,
        min_share=config.identify_min_session_share,
    )

    corrections = apply_neighbor_corrections(config=config, session_ids=[session_id])

    clusters = cluster_voiceprints(
        config=config,
        scope_session_id=session_id,
        min_cluster_size=config.identify_min_cluster_size,
    )

    return {
        "session_id": session_id,
        "excluded_absent": sorted(absent),
        "attributed": attributed,
        "pruned": pruned,
        "corrections_applied": int(corrections.get("applied", 0)),
        "clusters": clusters,
    }
