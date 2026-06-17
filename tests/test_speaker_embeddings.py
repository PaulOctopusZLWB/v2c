from __future__ import annotations

from pathlib import Path

import numpy as np

from personal_context_node import transcription
from personal_context_node.config import AppConfig
from personal_context_node.speaker_embeddings import (
    auto_attribute_enrolled,
    clear_projection_cache,
    embedding_projection,
    enroll_person,
    extract_pending_embeddings,
    get_embeddings,
    get_person_centroids,
    label_segments_as_person,
    pending_embedding_segment_ids,
    put_embedding,
    put_embeddings_bulk,
    recluster_by_anchors,
    suggest_people_for_session,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def test_put_get_roundtrip(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1"])

    vector = list(range(192))  # 192-dim
    put_embedding(config=config, segment_id="seg_1", vector=vector)

    result = get_embeddings(config=config, segment_ids=["seg_1"])
    assert set(result) == {"seg_1"}
    got = result["seg_1"]
    assert isinstance(got, np.ndarray)
    assert got.dtype == np.float32
    assert got.shape == (192,)
    np.testing.assert_allclose(got, np.asarray(vector, dtype=np.float32), atol=1e-5)

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select dim from segment_embeddings where segment_id = 'seg_1'")
    finally:
        conn.close()
    assert rows == [{"dim": 192}]


def test_put_embeddings_bulk(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    items = [
        ("seg_1", [0.1, 0.2, 0.3]),
        ("seg_2", [1.0, 2.0, 3.0]),
        ("seg_3", [4.0, 5.0, 6.0]),
    ]
    assert put_embeddings_bulk(config=config, items=items) == 3

    result = get_embeddings(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])
    assert set(result) == {"seg_1", "seg_2", "seg_3"}
    np.testing.assert_allclose(result["seg_2"], np.asarray([1.0, 2.0, 3.0], dtype=np.float32), atol=1e-5)


def test_get_embeddings_empty(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    assert get_embeddings(config=config, segment_ids=[]) == {}


def test_pending_lists_active_without_embedding(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    # No embeddings yet -> all three pending, ordered.
    assert pending_embedding_segment_ids(config=config) == ["seg_1", "seg_2", "seg_3"]

    put_embedding(config=config, segment_id="seg_1", vector=[0.0, 1.0, 2.0])
    assert pending_embedding_segment_ids(config=config) == ["seg_2", "seg_3"]

    put_embeddings_bulk(config=config, items=[("seg_2", [0.0]), ("seg_3", [1.0])])
    assert pending_embedding_segment_ids(config=config) == []

    # session_id scoping: only the matching session's pending segments are returned.
    _insert_session_with_segments(
        config.database_path, ["seg_o1", "seg_o2"], session_id="ses_other", audio_file_id="aud_other"
    )
    assert pending_embedding_segment_ids(config=config, session_id="ses_other") == ["seg_o1", "seg_o2"]
    assert pending_embedding_segment_ids(config=config, session_id="ses_test") == []


def test_extract_pending_embeds_all(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    monkeypatch.setattr(
        transcription,
        "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    embed_fn = lambda path: [0.1, 0.2, 0.3]

    result = extract_pending_embeddings(config=config, embed_fn=embed_fn)
    assert result == {"embedded": 3, "skipped_missing_audio": 0, "failed": 0, "total": 3}

    stored = get_embeddings(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])
    assert set(stored) == {"seg_1", "seg_2", "seg_3"}

    # A second pass has nothing left to embed.
    second = extract_pending_embeddings(config=config, embed_fn=embed_fn)
    assert second == {"embedded": 0, "skipped_missing_audio": 0, "failed": 0, "total": 0}


def test_extract_skips_missing_audio(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    def fake_path(*, config, segment_id):
        if segment_id == "seg_2":
            return None
        return Path(f"/slices/{segment_id}.wav")

    monkeypatch.setattr(transcription, "segment_audio_path", fake_path)
    embed_fn = lambda path: [0.1, 0.2, 0.3]

    result = extract_pending_embeddings(config=config, embed_fn=embed_fn)
    assert result == {"embedded": 2, "skipped_missing_audio": 1, "failed": 0, "total": 3}

    # The skipped segment stays pending; the embedded ones do not.
    assert pending_embedding_segment_ids(config=config) == ["seg_2"]


def test_extract_reports_progress(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    monkeypatch.setattr(
        transcription,
        "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    embed_fn = lambda path: [0.1, 0.2, 0.3]

    calls: list[tuple[int, int]] = []
    extract_pending_embeddings(
        config=config, embed_fn=embed_fn, progress=lambda done, total: calls.append((done, total))
    )

    assert len(calls) == 3
    assert calls[-1] == (3, 3)


def test_extract_scoped_by_session(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2"])
    _insert_session_with_segments(
        config.database_path, ["seg_o1", "seg_o2"], session_id="ses_other", audio_file_id="aud_other"
    )

    monkeypatch.setattr(
        transcription,
        "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    embed_fn = lambda path: [0.1, 0.2, 0.3]

    result = extract_pending_embeddings(config=config, embed_fn=embed_fn, session_id="ses_other")
    assert result == {"embedded": 2, "skipped_missing_audio": 0, "failed": 0, "total": 2}

    stored = get_embeddings(config=config, segment_ids=["seg_1", "seg_2", "seg_o1", "seg_o2"])
    assert set(stored) == {"seg_o1", "seg_o2"}
    assert pending_embedding_segment_ids(config=config, session_id="ses_test") == ["seg_1", "seg_2"]


def _unit_axis(index: int, *, noise: float = 0.0, dim: int = 192) -> list[float]:
    """A near-unit vector pointing along axis ``index`` with a little noise on a few other axes.

    Vectors built around different axes stay clearly separable (cosine ordering unambiguous)
    while the small noise makes them non-degenerate.
    """
    vec = np.zeros(dim, dtype=np.float64)
    vec[index] = 1.0
    # Sprinkle a small, deterministic amount of noise on neighbouring axes.
    for offset in (1, 2, 3):
        vec[(index + offset) % dim] += noise * (offset / 3.0)
    return vec.tolist()


def _insert_persons(database_path: Path, persons: dict[str, str]) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        for person_id, display_name in persons.items():
            conn.execute(
                "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
                (person_id, display_name, "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def _override_rows(database_path: Path) -> dict[str, dict[str, str]]:
    conn = connect(database_path)
    try:
        rows = fetch_all(conn, "select segment_id, person_id, person_label from segment_person_overrides")
    finally:
        conn.close()
    return {str(r["segment_id"]): {"person_id": str(r["person_id"]), "person_label": str(r["person_label"])} for r in rows}


def _setup_two_clusters(tmp_path: Path) -> AppConfig:
    """6 segments: seg_1..3 near axis e0 (personA), seg_4..6 near axis e1 (personB)."""
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3", "seg_4", "seg_5", "seg_6"])
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob"})

    put_embeddings_bulk(
        config=config,
        items=[
            ("seg_1", _unit_axis(0, noise=0.05)),
            ("seg_2", _unit_axis(0, noise=0.20)),
            ("seg_3", _unit_axis(0, noise=0.30)),
            ("seg_4", _unit_axis(1, noise=0.05)),
            ("seg_5", _unit_axis(1, noise=0.20)),
            ("seg_6", _unit_axis(1, noise=0.30)),
        ],
    )
    return config


def test_recluster_two_clear_clusters(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)

    result = recluster_by_anchors(
        config=config,
        anchors={"seg_1": "per_a", "seg_4": "per_b"},
        threshold=0.5,
    )

    assert result["total"] == 6
    assert result["assigned"] == 6
    assert result["unassigned"] == 0
    assert result["per_person"] == {"per_a": 3, "per_b": 3}
    assert result["threshold"] == 0.5

    overrides = _override_rows(config.database_path)
    assert overrides["seg_1"]["person_id"] == "per_a"
    assert overrides["seg_2"]["person_id"] == "per_a"
    assert overrides["seg_3"]["person_id"] == "per_a"
    assert overrides["seg_4"]["person_id"] == "per_b"
    assert overrides["seg_5"]["person_id"] == "per_b"
    assert overrides["seg_6"]["person_id"] == "per_b"
    # person_label resolved from persons.display_name.
    assert overrides["seg_1"]["person_label"] == "Alice"
    assert overrides["seg_4"]["person_label"] == "Bob"


def test_recluster_high_threshold_leaves_unassigned(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)

    result = recluster_by_anchors(
        config=config,
        anchors={"seg_1": "per_a", "seg_4": "per_b"},
        threshold=0.999,
    )

    assert result["total"] == 6
    assert result["unassigned"] > 0
    # Anchors are always assigned to their labelled person regardless of threshold.
    overrides = _override_rows(config.database_path)
    assert overrides["seg_1"]["person_id"] == "per_a"
    assert overrides["seg_4"]["person_id"] == "per_b"
    assert result["assigned"] >= 2
    assert result["assigned"] + result["unassigned"] == result["total"]


def test_recluster_empty_anchors_raises(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    try:
        recluster_by_anchors(config=config, anchors={}, threshold=0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty anchors")


def test_recluster_bad_threshold_raises(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    try:
        recluster_by_anchors(config=config, anchors={"seg_1": "per_a"}, threshold=1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for out-of-range threshold")


def test_recluster_does_not_touch_speaker_columns(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)

    conn = connect(config.database_path)
    try:
        before = fetch_all(conn, "select segment_id, speaker, speaker_cluster_id from transcript_segments order by segment_id")
    finally:
        conn.close()

    recluster_by_anchors(
        config=config,
        anchors={"seg_1": "per_a", "seg_4": "per_b"},
        threshold=0.5,
    )

    conn = connect(config.database_path)
    try:
        after = fetch_all(conn, "select segment_id, speaker, speaker_cluster_id from transcript_segments order by segment_id")
    finally:
        conn.close()

    assert before == after


def _write_raw_embedding(database_path: Path, segment_id: str, values: list[float]) -> None:
    """Overwrite a segment's stored embedding blob directly, bypassing the write-time guards
    (so we can exercise the read/recluster path against a corrupt or wrong-dim vector)."""
    array = np.asarray(values, dtype=np.float32)
    conn = connect(database_path)
    try:
        conn.execute(
            "update segment_embeddings set vector = ?, dim = ? where segment_id = ?",
            (array.tobytes(), len(array), segment_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_put_embedding_rejects_non_finite(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1"])
    bad = [float("nan")] + [0.0] * 191
    for raises in (
        lambda: put_embedding(config=config, segment_id="seg_1", vector=bad),
        lambda: put_embeddings_bulk(config=config, items=[("seg_1", bad)]),
    ):
        try:
            raises()
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for non-finite embedding")


def test_recluster_nan_embedding_left_unassigned(tmp_path: Path) -> None:
    # A corrupt (NaN) scope embedding must NOT be force-assigned to person 0 (nan < threshold is
    # False); it should fall through as unassigned.
    config = _setup_two_clusters(tmp_path)
    _write_raw_embedding(config.database_path, "seg_3", [float("nan")] + [0.0] * 191)

    result = recluster_by_anchors(config=config, anchors={"seg_1": "per_a", "seg_4": "per_b"}, threshold=0.5)

    overrides = _override_rows(config.database_path)
    assert "seg_3" not in overrides  # corrupt vector left unassigned, not mis-attributed
    assert result["assigned"] == 5
    assert result["unassigned"] == 1


def test_recluster_skips_mismatched_dim(tmp_path: Path) -> None:
    # A scope vector with a different dimensionality (e.g. a future re-embed) is skipped, not fed
    # to the matmul (which would otherwise crash the whole pass).
    config = _setup_two_clusters(tmp_path)
    _write_raw_embedding(config.database_path, "seg_3", [0.1] * 64)  # wrong dim (anchors are 192)

    result = recluster_by_anchors(config=config, anchors={"seg_1": "per_a", "seg_4": "per_b"}, threshold=0.5)

    overrides = _override_rows(config.database_path)
    assert "seg_3" not in overrides
    assert result["assigned"] == 5  # the other 5 still attributed


def test_extract_continues_past_failed_embed(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])
    monkeypatch.setattr(
        transcription, "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )

    def embed_fn(path: str) -> list[float]:
        if "seg_2" in path:
            raise RuntimeError("CAM++ failed on this slice")
        return [0.1, 0.2, 0.3]

    result = extract_pending_embeddings(config=config, embed_fn=embed_fn)
    assert result == {"embedded": 2, "skipped_missing_audio": 0, "failed": 1, "total": 3}
    assert set(get_embeddings(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])) == {"seg_1", "seg_3"}
    assert pending_embedding_segment_ids(config=config) == ["seg_2"]  # the failed one stays pending


def test_get_embeddings_chunks_large_input(tmp_path: Path) -> None:
    # >999 ids must not trip SQLite's per-statement bind-variable limit.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    ids = [f"seg_{i:04d}" for i in range(1200)]
    _insert_session_with_segments(config.database_path, ids)
    put_embeddings_bulk(config=config, items=[(sid, [0.1, 0.2, 0.3]) for sid in ids])

    got = get_embeddings(config=config, segment_ids=ids)
    assert len(got) == 1200


def test_projection_pca_separates_two_clusters(tmp_path: Path) -> None:
    clear_projection_cache()
    config = _setup_two_clusters(tmp_path)

    result = embedding_projection(config=config, method="pca")

    assert result["method"] == "pca"
    assert result["n"] == 6
    points = result["points"]
    assert len(points) == 6
    by_id = {p["segment_id"]: p for p in points}
    for point in points:
        assert 0.0 <= point["x"] <= 1.0
        assert 0.0 <= point["y"] <= 1.0
        assert point["speaker"] == "self"
    # The two clusters (seg_1..3 near e0, seg_4..6 near e1) should be separated along x.
    cluster_a_x = np.mean([by_id[s]["x"] for s in ("seg_1", "seg_2", "seg_3")])
    cluster_b_x = np.mean([by_id[s]["x"] for s in ("seg_4", "seg_5", "seg_6")])
    assert abs(cluster_a_x - cluster_b_x) > 0.3


def test_projection_empty_scope(tmp_path: Path) -> None:
    clear_projection_cache()
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    result = embedding_projection(config=config, method="pca")
    assert result == {"points": [], "method": "pca", "n": 0}


def test_projection_includes_person_attribution(tmp_path: Path) -> None:
    clear_projection_cache()
    config = _setup_two_clusters(tmp_path)
    # Attribute seg_1 to a person via a segment_person_overrides row; leave the rest unattributed.
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id) values (?, ?, ?, ?)",
            ("seg_1", "Alice", "2087-05-10T08:00:00+08:00", "per_a"),
        )
        conn.commit()
    finally:
        conn.close()

    points = {p["segment_id"]: p for p in embedding_projection(config=config, method="pca")["points"]}
    assert points["seg_1"]["person_id"] == "per_a"
    assert points["seg_1"]["person_label"] == "Alice"
    assert points["seg_2"]["person_id"] is None
    assert points["seg_2"]["person_label"] is None
    # text is carried through (and truncated, but these are short).
    assert points["seg_1"]["text"] == "text 1"


def test_projection_cache_hit(tmp_path: Path) -> None:
    clear_projection_cache()
    config = _setup_two_clusters(tmp_path)

    first = embedding_projection(config=config, method="pca")
    second = embedding_projection(config=config, method="pca")
    # Deterministic + cached: the second call returns the very same object.
    assert second is first

    clear_projection_cache()
    third = embedding_projection(config=config, method="pca")
    # After a cache clear it is recomputed (a fresh object) but identical in content.
    assert third is not first
    assert third == first


# ---------------------------------------------------------------------------
# Slice 5a: enroll / suggest / auto-attribute + bulk label-segments
# ---------------------------------------------------------------------------


def test_label_segments_as_person_writes_overrides(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)

    n = label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_1", "seg_2", "seg_3"])
    assert n == 3

    overrides = _override_rows(config.database_path)
    assert overrides["seg_1"] == {"person_id": "per_a", "person_label": "Alice"}
    assert overrides["seg_2"] == {"person_id": "per_a", "person_label": "Alice"}
    assert overrides["seg_3"] == {"person_id": "per_a", "person_label": "Alice"}
    # The other cluster is untouched.
    assert "seg_4" not in overrides


def test_label_segments_empty_is_zero(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    assert label_segments_as_person(config=config, person_id="per_a", segment_ids=[]) == 0
    assert _override_rows(config.database_path) == {}


def test_label_segments_unknown_person_raises(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    try:
        label_segments_as_person(config=config, person_id="ghost", segment_ids=["seg_1"])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown person")


def test_enroll_person_with_explicit_segments(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)

    result = enroll_person(config=config, person_id="per_a", segment_ids=["seg_1", "seg_2", "seg_3"])
    assert result["person_id"] == "per_a"
    assert result["n_segments"] == 3
    assert result["dim"] == 192

    centroids = get_person_centroids(config=config)
    assert set(centroids) == {"per_a"}
    centroid = centroids["per_a"]
    assert centroid.shape == (192,)
    np.testing.assert_allclose(np.linalg.norm(centroid), 1.0, atol=1e-6)
    # The cluster sits near axis e0, so the centroid's dominant component is e0.
    assert int(np.argmax(centroid)) == 0


def test_enroll_person_uses_attributed_when_no_ids(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    # Attribute seg_4, seg_5 (the e1 cluster) to per_b via overrides.
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_4", "seg_5"])

    result = enroll_person(config=config, person_id="per_b")
    assert result["n_segments"] == 2
    assert result["dim"] == 192

    centroid = get_person_centroids(config=config)["per_b"]
    assert int(np.argmax(centroid)) == 1  # e1 cluster


def test_enroll_person_no_embeddings_raises(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    # per_a has no attributed segments and no explicit ids -> nothing to enroll.
    try:
        enroll_person(config=config, person_id="per_a")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when no embeddings found")


def test_get_person_centroids_roundtrips_unit_vectors(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    enroll_person(config=config, person_id="per_a", segment_ids=["seg_1", "seg_2", "seg_3"])
    enroll_person(config=config, person_id="per_b", segment_ids=["seg_4", "seg_5", "seg_6"])

    centroids = get_person_centroids(config=config)
    assert set(centroids) == {"per_a", "per_b"}
    for vec in centroids.values():
        np.testing.assert_allclose(np.linalg.norm(vec), 1.0, atol=1e-6)


def test_suggest_people_for_session_maps_clusters(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    enroll_person(config=config, person_id="per_a", segment_ids=["seg_1", "seg_2", "seg_3"])
    enroll_person(config=config, person_id="per_b", segment_ids=["seg_4", "seg_5", "seg_6"])

    # Build a separate session whose two speakers are A-like / B-like.
    _insert_session_with_segments(
        config.database_path, ["seg_qa", "seg_qb"], session_id="ses_query", audio_file_id="aud_query"
    )
    _set_speaker(config.database_path, "seg_qa", "spk_qa")
    _set_speaker(config.database_path, "seg_qb", "spk_qb")
    put_embeddings_bulk(
        config=config,
        items=[("seg_qa", _unit_axis(0, noise=0.1)), ("seg_qb", _unit_axis(1, noise=0.1))],
    )

    result = suggest_people_for_session(config=config, session_id="ses_query")
    by_speaker = {s["speaker"]: s for s in result["suggestions"]}
    assert by_speaker["spk_qa"]["person_id"] == "per_a"
    assert by_speaker["spk_qa"]["person_label"] == "Alice"
    assert by_speaker["spk_qa"]["score"] > 0
    assert by_speaker["spk_qb"]["person_id"] == "per_b"
    assert by_speaker["spk_qb"]["person_label"] == "Bob"
    # Sorted by score desc.
    scores = [s["score"] for s in result["suggestions"]]
    assert scores == sorted(scores, reverse=True)


def test_suggest_people_no_enrolled_is_empty(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    # No one enrolled.
    result = suggest_people_for_session(config=config, session_id="ses_test")
    assert result == {"suggestions": []}


def test_auto_attribute_enrolled_assigns_all(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    enroll_person(config=config, person_id="per_a", segment_ids=["seg_1", "seg_2", "seg_3"])
    enroll_person(config=config, person_id="per_b", segment_ids=["seg_4", "seg_5", "seg_6"])

    result = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)
    assert result["total"] == 6
    assert result["assigned"] == 6
    assert result["unassigned"] == 0
    assert result["per_person"] == {"per_a": 3, "per_b": 3}
    assert result["threshold"] == 0.5

    overrides = _override_rows(config.database_path)
    assert overrides["seg_1"]["person_id"] == "per_a"
    assert overrides["seg_6"]["person_id"] == "per_b"
    assert overrides["seg_1"]["person_label"] == "Alice"
    assert overrides["seg_6"]["person_label"] == "Bob"


def test_auto_attribute_high_threshold_leaves_unassigned(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    enroll_person(config=config, person_id="per_a", segment_ids=["seg_1", "seg_2", "seg_3"])
    enroll_person(config=config, person_id="per_b", segment_ids=["seg_4", "seg_5", "seg_6"])

    result = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.999)
    assert result["total"] == 6
    assert result["unassigned"] > 0
    assert result["assigned"] + result["unassigned"] == result["total"]


def test_auto_attribute_no_enrolled_raises(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    try:
        auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when no people enrolled")


def _set_speaker(database_path: Path, segment_id: str, speaker: str) -> None:
    conn = connect(database_path)
    try:
        conn.execute(
            "update transcript_segments set speaker = ?, speaker_cluster_id = ? where segment_id = ?",
            (speaker, speaker, segment_id),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_session_with_segments(
    database_path: Path,
    segment_ids: list[str],
    *,
    session_id: str = "ses_test",
    audio_file_id: str = "aud_test",
    date_key: str = "2087-05-10",
) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (audio_file_id, "DJI Mic 3", f"/source/{audio_file_id}.wav", 1, 1, f"/raw/{audio_file_id}.wav", f"sha256:{audio_file_id}", 2000, f"{date_key}T08:00:00+08:00", f"{date_key}T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, date_key, f"{date_key}T08:00:00+08:00", f"{date_key}T08:00:02+08:00", "derived_from_segments", len(segment_ids), 2000, segment_ids[0], f"{date_key}T08:00:03+08:00", f"{date_key}T08:00:03+08:00"),
        )
        for index, segment_id in enumerate(segment_ids):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, absolute_end_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, audio_file_id, f"chk_{segment_id}", session_id, index * 1000, (index + 1) * 1000, f"{date_key}T08:00:0{index}.000000+08:00", f"{date_key}T08:00:0{index + 1}.000000+08:00", f"text {index + 1}", "zh", "self", "self", f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, f"{date_key}T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()
