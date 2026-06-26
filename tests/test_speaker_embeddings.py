from __future__ import annotations

from pathlib import Path

import numpy as np

from personal_context_node import transcription
from personal_context_node.config import AppConfig
from personal_context_node.segment_emotions import get_emotions, pending_emotion_segment_ids
from personal_context_node.speaker_embeddings import (
    auto_attribute_enrolled,
    apply_neighbor_corrections,
    clear_segment_person_attributions,
    clear_projection_cache,
    assign_cluster_to_person,
    cluster_voiceprints,
    embedding_projection,
    enroll_person,
    ensure_person,
    extract_pending_embeddings,
    extract_pending_embeddings_and_emotions,
    get_embeddings,
    global_clusters,
    identification_status,
    get_person_centroids,
    label_segments_as_person,
    mark_noise_segments,
    pending_embedding_segment_ids,
    preview_neighbor_corrections,
    project_embeddings,
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


def test_combined_extraction_processes_union_of_pending(tmp_path: Path, monkeypatch) -> None:
    # seg_1: pending both; seg_2: already embedded (pending emotion only);
    # seg_3: already emoted (pending embedding only) -> union is all three, but each artifact's
    # own "total" only counts what THAT artifact was actually missing.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])
    put_embeddings_bulk(config=config, items=[("seg_2", [0.1, 0.2, 0.3])])
    from personal_context_node.segment_emotions import put_emotions_bulk

    put_emotions_bulk(config=config, items=[("seg_3", {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}})])

    monkeypatch.setattr(
        transcription, "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    embed_fn = lambda path: [0.4, 0.5, 0.6]
    classify_fn = lambda path: {"label": "开心/happy", "scores": {"开心/happy": 1.0}}

    result = extract_pending_embeddings_and_emotions(
        config=config, embed_fn=embed_fn, classify_fn=classify_fn,
    )

    # embedding was pending for seg_1 + seg_3 (seg_2 already had one).
    assert result["embedding"]["embedded"] == 2
    assert result["embedding"]["total"] == 2
    # emotion was pending for seg_1 + seg_2 (seg_3 already had one).
    assert result["emotion"]["emoted"] == 2
    assert result["emotion"]["total"] == 2

    assert set(get_embeddings(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])) == {
        "seg_1", "seg_2", "seg_3",
    }
    assert set(get_emotions(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])) == {
        "seg_1", "seg_2", "seg_3",
    }
    assert pending_embedding_segment_ids(config=config) == []
    assert pending_emotion_segment_ids(config=config) == []


def test_combined_extraction_resolves_audio_path_once_per_segment(tmp_path: Path, monkeypatch) -> None:
    # The whole point of combining the two loops is to halve audio-path resolution when a segment
    # needs BOTH artifacts -- assert segment_audio_path is called exactly once per segment even
    # though both embed_fn and classify_fn are invoked for it.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    calls: list[str] = []

    def fake_path(*, config, segment_id):
        calls.append(segment_id)
        return Path(f"/slices/{segment_id}.wav")

    monkeypatch.setattr(transcription, "segment_audio_path", fake_path)
    embed_fn = lambda path: [0.1, 0.2, 0.3]
    classify_fn = lambda path: {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    extract_pending_embeddings_and_emotions(config=config, embed_fn=embed_fn, classify_fn=classify_fn)

    assert sorted(calls) == ["seg_1", "seg_2", "seg_3"]  # exactly once each, not twice


def test_combined_extraction_one_bad_segment_does_not_abort_others(tmp_path: Path, monkeypatch) -> None:
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

    def classify_fn(path: str) -> dict:
        if "seg_3" in path:
            raise RuntimeError("emotion2vec failed on this slice")
        return {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    result = extract_pending_embeddings_and_emotions(
        config=config, embed_fn=embed_fn, classify_fn=classify_fn,
    )

    assert result["embedding"] == {"embedded": 2, "skipped_missing_audio": 0, "failed": 1, "total": 3}
    assert result["emotion"] == {"emoted": 2, "skipped_missing_audio": 0, "failed": 1, "total": 3}
    assert pending_embedding_segment_ids(config=config) == ["seg_2"]
    assert pending_emotion_segment_ids(config=config) == ["seg_3"]


def test_combined_extraction_skips_missing_audio_for_both(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    def fake_path(*, config, segment_id):
        if segment_id == "seg_2":
            return None
        return Path(f"/slices/{segment_id}.wav")

    monkeypatch.setattr(transcription, "segment_audio_path", fake_path)
    embed_fn = lambda path: [0.1, 0.2, 0.3]
    classify_fn = lambda path: {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    result = extract_pending_embeddings_and_emotions(
        config=config, embed_fn=embed_fn, classify_fn=classify_fn,
    )

    assert result["embedding"] == {"embedded": 2, "skipped_missing_audio": 1, "failed": 0, "total": 3}
    assert result["emotion"] == {"emoted": 2, "skipped_missing_audio": 1, "failed": 0, "total": 3}
    assert pending_embedding_segment_ids(config=config) == ["seg_2"]
    assert pending_emotion_segment_ids(config=config) == ["seg_2"]


def test_combined_extraction_reports_progress_over_union(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])
    monkeypatch.setattr(
        transcription, "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    embed_fn = lambda path: [0.1, 0.2, 0.3]
    classify_fn = lambda path: {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    calls: list[tuple[int, int]] = []
    extract_pending_embeddings_and_emotions(
        config=config, embed_fn=embed_fn, classify_fn=classify_fn,
        progress=lambda done, total: calls.append((done, total)),
    )

    assert len(calls) == 3  # union size, not embedding total + emotion total
    assert calls[-1] == (3, 3)


def test_combined_extraction_second_pass_has_nothing_left(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2"])
    monkeypatch.setattr(
        transcription, "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    embed_fn = lambda path: [0.1, 0.2, 0.3]
    classify_fn = lambda path: {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    first = extract_pending_embeddings_and_emotions(config=config, embed_fn=embed_fn, classify_fn=classify_fn)
    assert first["embedding"]["embedded"] == 2
    assert first["emotion"]["emoted"] == 2

    second = extract_pending_embeddings_and_emotions(config=config, embed_fn=embed_fn, classify_fn=classify_fn)
    assert second["embedding"] == {"embedded": 0, "skipped_missing_audio": 0, "failed": 0, "total": 0}
    assert second["emotion"] == {"emoted": 0, "skipped_missing_audio": 0, "failed": 0, "total": 0}


def test_combined_extraction_scoped_by_session(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2"])
    _insert_session_with_segments(
        config.database_path, ["seg_o1", "seg_o2"], session_id="ses_other", audio_file_id="aud_other"
    )
    monkeypatch.setattr(
        transcription, "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )
    embed_fn = lambda path: [0.1, 0.2, 0.3]
    classify_fn = lambda path: {"label": "中立/neutral", "scores": {"中立/neutral": 1.0}}

    result = extract_pending_embeddings_and_emotions(
        config=config, embed_fn=embed_fn, classify_fn=classify_fn, session_id="ses_other",
    )

    assert result["embedding"]["embedded"] == 2
    assert result["emotion"]["emoted"] == 2
    assert set(get_embeddings(config=config, segment_ids=["seg_1", "seg_2", "seg_o1", "seg_o2"])) == {
        "seg_o1", "seg_o2",
    }
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


def _seed_neighbor_correction_fixture(config: AppConfig, *, manual_wrong: bool = False) -> None:
    _insert_session_with_segments(config.database_path, ["a_1", "a_2", "a_3", "a_wrong", "b_1", "b_2"])
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob"})
    put_embeddings_bulk(
        config=config,
        items=[
            ("a_1", [1.0, 0.0, 0.0]),
            ("a_2", [0.98, 0.02, 0.0]),
            ("a_3", [0.96, 0.01, 0.0]),
            ("a_wrong", [0.97, 0.03, 0.0]),
            ("b_1", [0.0, 1.0, 0.0]),
            ("b_2", [0.02, 0.98, 0.0]),
        ],
    )
    now = "2087-05-10T08:00:00+08:00"
    wrong_source = "manual" if manual_wrong else "voiceprint"
    rows = [
        ("a_1", "Alice", "per_a", "manual"),
        ("a_2", "Alice", "per_a", "manual"),
        ("a_3", "Alice", "per_a", "manual"),
        ("a_wrong", "Bob", "per_b", wrong_source),
        ("b_1", "Bob", "per_b", "manual"),
        ("b_2", "Bob", "per_b", "manual"),
    ]
    conn = connect(config.database_path)
    try:
        conn.executemany(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values (?, ?, ?, ?, ?)",
            [(segment_id, label, now, person_id, source) for segment_id, label, person_id, source in rows],
        )
        conn.commit()
    finally:
        conn.close()


def test_clear_segment_person_attributions_removes_only_selected_overrides(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_a", "seg_b", "seg_c"])
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob"})
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values (?, ?, ?, ?, ?)",
            ("seg_a", "Alice", "2087-05-10T08:00:00+08:00", "per_a", "manual"),
        )
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values (?, ?, ?, ?, ?)",
            ("seg_b", "Bob", "2087-05-10T08:00:00+08:00", "per_b", "voiceprint"),
        )
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values (?, ?, ?, ?, ?)",
            ("seg_c", "Bob", "2087-05-10T08:00:00+08:00", "per_b", "voiceprint"),
        )
        conn.commit()
    finally:
        conn.close()

    result = clear_segment_person_attributions(config=config, segment_ids=["seg_a", "seg_b", "missing"])

    assert result == {"cleared": 2}
    rows = _override_rows(config.database_path)
    assert set(rows) == {"seg_c"}
    assert rows["seg_c"]["person_id"] == "per_b"


def test_preview_neighbor_corrections_fixes_isolated_voiceprint_mislabel(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_neighbor_correction_fixture(config)

    preview = preview_neighbor_corrections(
        config=config,
        session_ids=["ses_test"],
        k=5,
        min_neighbours=3,
        majority_ratio=0.6,
        similarity_floor=0.3,
    )

    assert preview["changed"] == 1
    assert preview["groups"] == [
        {
            "from_person_id": "per_b",
            "from_person_label": "Bob",
            "to_person_id": "per_a",
            "to_person_label": "Alice",
            "count": 1,
            "segment_ids": ["a_wrong"],
        }
    ]
    assert preview["corrections"][0]["segment_id"] == "a_wrong"


def test_apply_neighbor_corrections_preserves_manual_labels(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_neighbor_correction_fixture(config, manual_wrong=True)

    result = apply_neighbor_corrections(
        config=config,
        session_ids=["ses_test"],
        k=5,
        min_neighbours=3,
        majority_ratio=0.6,
        similarity_floor=0.3,
    )

    assert result["changed"] == 0
    rows = _override_rows(config.database_path)
    assert rows["a_wrong"]["person_id"] == "per_b"


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


def test_extract_continues_past_non_finite_embedding(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])
    monkeypatch.setattr(
        transcription, "segment_audio_path",
        lambda *, config, segment_id: Path(f"/slices/{segment_id}.wav"),
    )

    def embed_fn(path: str) -> list[float]:
        if "seg_2" in path:
            return [float("nan"), 0.2, 0.3]
        return [0.1, 0.2, 0.3]

    result = extract_pending_embeddings(config=config, embed_fn=embed_fn)
    assert result == {"embedded": 2, "skipped_missing_audio": 0, "failed": 1, "total": 3}
    assert set(get_embeddings(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])) == {"seg_1", "seg_3"}
    assert pending_embedding_segment_ids(config=config) == ["seg_2"]


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


def test_projection_cache_invalidated_by_attribution_write(tmp_path: Path) -> None:
    # The map refetches the projection right after a lasso-label / recluster; the cache MUST NOT
    # return a stale projection that still shows the old attribution (the cache key doesn't change
    # because the embedding set is unchanged — only the override write does).
    clear_projection_cache()
    config = _setup_two_clusters(tmp_path)

    before = {p["segment_id"]: p for p in embedding_projection(config=config, method="pca")["points"]}
    assert before["seg_2"]["person_label"] is None  # unattributed, and now cached

    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_2"])

    after = {p["segment_id"]: p for p in embedding_projection(config=config, method="pca")["points"]}
    assert after["seg_2"]["person_id"] == "per_a"      # fresh, not the stale cached None
    assert after["seg_2"]["person_label"] == "Alice"


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
    # kNN needs labeled (manual) segments: one seed per cluster.
    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_1"])
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_4"])

    result = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)
    assert result["total"] == 6
    # The 4 unlabeled segments are all assigned by voiceprint (manual seeds counted separately).
    assert result["assigned"] == 4
    assert result["per_person"] == {"per_a": 2, "per_b": 2}
    assert result["threshold"] == 0.5

    overrides = _override_rows(config.database_path)
    assert overrides["seg_1"]["person_id"] == "per_a"
    assert overrides["seg_6"]["person_id"] == "per_b"
    assert overrides["seg_1"]["person_label"] == "Alice"
    assert overrides["seg_6"]["person_label"] == "Bob"


def test_auto_attribute_high_threshold_leaves_unassigned(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_1"])
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_4"])

    result = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.999)
    assert result["total"] == 6
    assert result["unassigned"] > 0


def test_auto_attribute_no_enrolled_raises(tmp_path: Path) -> None:
    config = _setup_two_clusters(tmp_path)
    try:
        auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when no labeled segments")


# ---------------------------------------------------------------------------
# Supervised global identity: manual vs voiceprint source
# ---------------------------------------------------------------------------


def _override_sources(database_path: Path) -> dict[str, str]:
    conn = connect(database_path)
    try:
        rows = fetch_all(conn, "select segment_id, source from segment_person_overrides")
    finally:
        conn.close()
    return {str(r["segment_id"]): str(r["source"]) for r in rows}


def _voiceprint_exists(database_path: Path, person_id: str) -> bool:
    conn = connect(database_path)
    try:
        rows = fetch_all(conn, "select 1 from person_voiceprints where person_id = ?", (person_id,))
    finally:
        conn.close()
    return bool(rows)


def test_label_segments_writes_manual_source_and_enrolls(tmp_path: Path) -> None:
    # Labelling IS the ground-truth signal: each override gets source='manual', and the person's
    # voiceprint is enrolled immediately so the centroid reflects the new labels right away.
    config = _setup_two_clusters(tmp_path)

    n = label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_1", "seg_2"])
    assert n == 2

    sources = _override_sources(config.database_path)
    assert sources["seg_1"] == "manual"
    assert sources["seg_2"] == "manual"
    # Enrolled immediately from the just-written manual labels.
    assert _voiceprint_exists(config.database_path, "per_a")
    centroid = get_person_centroids(config=config)["per_a"]
    assert int(np.argmax(centroid)) == 0  # e0 cluster


def test_enroll_person_no_ids_uses_only_manual_rows(tmp_path: Path) -> None:
    # When segment_ids is None, enroll gathers ONLY source='manual' overrides (confirmed labels),
    # never source='voiceprint' guesses — so an inferred attribution cannot drift the centroid.
    config = _setup_two_clusters(tmp_path)
    now = "2087-05-10T08:00:00+08:00"
    conn = connect(config.database_path)
    try:
        # seg_4 is a confirmed manual label for per_b; seg_5 is an auto-inferred voiceprint guess.
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) "
            "values (?, ?, ?, ?, ?)",
            ("seg_4", "Bob", now, "per_b", "manual"),
        )
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) "
            "values (?, ?, ?, ?, ?)",
            ("seg_5", "Bob", now, "per_b", "voiceprint"),
        )
        conn.commit()
    finally:
        conn.close()

    result = enroll_person(config=config, person_id="per_b")
    assert result["n_segments"] == 1  # only the manual seg_4, NOT the voiceprint seg_5


def test_identify_assigns_rest_by_voiceprint_respecting_manual(tmp_path: Path) -> None:
    # Two persons each manually labelled with ONE segment; identify enrolls them from those labels,
    # then assigns every other in-scope embedded segment by nearest centroid with source='voiceprint'.
    config = _setup_two_clusters(tmp_path)
    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_1"])
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_4"])

    result = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)

    sources = _override_sources(config.database_path)
    overrides = _override_rows(config.database_path)
    # Manual seeds keep source='manual' and their labelled person.
    assert sources["seg_1"] == "manual"
    assert sources["seg_4"] == "manual"
    assert overrides["seg_1"]["person_id"] == "per_a"
    assert overrides["seg_4"]["person_id"] == "per_b"
    # The remaining segments are inferred by voiceprint.
    for seg in ("seg_2", "seg_3"):
        assert sources[seg] == "voiceprint"
        assert overrides[seg]["person_id"] == "per_a"
    for seg in ("seg_5", "seg_6"):
        assert sources[seg] == "voiceprint"
        assert overrides[seg]["person_id"] == "per_b"
    # per_person counts only the voiceprint assignments (manual seeds are separate).
    assert result["per_person"] == {"per_a": 2, "per_b": 2}
    assert result["assigned"] == 4
    assert result["threshold"] == 0.5


def test_identify_idempotent_and_manual_always_wins(tmp_path: Path) -> None:
    # Re-running identify clears prior voiceprint guesses (no accumulation) and never overwrites a
    # manual label. Labelling a previously-inferred segment manually then re-identifying keeps it manual.
    config = _setup_two_clusters(tmp_path)
    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_1"])
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_4"])

    first = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)
    # seg_2 was inferred as per_a by voiceprint; now the user confirms it as per_b manually.
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_2"])
    assert _override_sources(config.database_path)["seg_2"] == "manual"

    second = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)

    sources = _override_sources(config.database_path)
    overrides = _override_rows(config.database_path)
    # seg_2 stays the manual per_b label; identify never reverted it to a voiceprint guess.
    assert sources["seg_2"] == "manual"
    assert overrides["seg_2"]["person_id"] == "per_b"
    # No stale accumulation: every row is still exactly one of the 6 segments.
    assert set(sources) == {"seg_1", "seg_2", "seg_3", "seg_4", "seg_5", "seg_6"}
    # The original manual seeds are untouched.
    assert sources["seg_1"] == "manual"
    assert sources["seg_4"] == "manual"
    # Idempotent counts: total stays 6 across re-runs.
    assert first["total"] == 6
    assert second["total"] == 6


# ---------------------------------------------------------------------------
# kNN global identify (items 4): nearest labeled-segment vote, not single centroid
# ---------------------------------------------------------------------------


def test_knn_identify_two_clusters_by_nearest_labels(tmp_path: Path) -> None:
    # One manual label per cluster; kNN over labeled segments assigns the rest by nearest-labels
    # vote. e0-cluster -> per_a, e1-cluster -> per_b. Manual rows keep source='manual'.
    config = _setup_two_clusters(tmp_path)
    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_1"])
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_4"])

    result = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)

    sources = _override_sources(config.database_path)
    overrides = _override_rows(config.database_path)
    assert sources["seg_1"] == "manual"
    assert sources["seg_4"] == "manual"
    for seg in ("seg_2", "seg_3"):
        assert sources[seg] == "voiceprint"
        assert overrides[seg]["person_id"] == "per_a"
    for seg in ("seg_5", "seg_6"):
        assert sources[seg] == "voiceprint"
        assert overrides[seg]["person_id"] == "per_b"
    assert result["per_person"] == {"per_a": 2, "per_b": 2}
    assert result["assigned"] == 4
    assert result["threshold"] == 0.5


def test_knn_identify_idempotent_on_rerun(tmp_path: Path) -> None:
    # Re-running clears prior voiceprint guesses (no accumulation) and never touches manual labels.
    config = _setup_two_clusters(tmp_path)
    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_1"])
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_4"])

    first = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)
    second = auto_attribute_enrolled(config=config, session_id="ses_test", threshold=0.5)

    sources = _override_sources(config.database_path)
    assert set(sources) == {"seg_1", "seg_2", "seg_3", "seg_4", "seg_5", "seg_6"}
    assert sources["seg_1"] == "manual"
    assert sources["seg_4"] == "manual"
    assert first == second


def test_knn_far_point_stays_unassigned_by_cosine_floor(tmp_path: Path) -> None:
    # A third point far from BOTH labeled axes (axis e100) must stay unassigned: even though kNN
    # would vote it to the nearest cluster, its best single cosine is below the 0.25 floor.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3", "seg_4", "seg_far"])
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob"})
    put_embeddings_bulk(
        config=config,
        items=[
            ("seg_1", _unit_axis(0, noise=0.05)),
            ("seg_2", _unit_axis(0, noise=0.20)),
            ("seg_3", _unit_axis(1, noise=0.05)),
            ("seg_4", _unit_axis(1, noise=0.20)),
            ("seg_far", _unit_axis(100, noise=0.0)),  # orthogonal to both labeled axes
        ],
    )
    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_1"])
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_3"])

    auto_attribute_enrolled(config=config, threshold=0.5)

    overrides = _override_rows(config.database_path)
    assert "seg_far" not in overrides  # below cosine floor -> unassigned


def test_knn_multimodal_person_handled(tmp_path: Path) -> None:
    # per_a is multi-modal: TWO labeled segments on different axes (e0 and e50). An unlabeled seg
    # near e50 still goes to per_a — a kNN nearest-label vote handles this where a single centroid
    # (averaging e0 and e50) would sit between them and miss.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_a0", "seg_a50", "seg_b", "seg_q50"])
    _insert_persons(config.database_path, {"per_a": "Alice", "per_b": "Bob"})
    put_embeddings_bulk(
        config=config,
        items=[
            ("seg_a0", _unit_axis(0, noise=0.05)),    # per_a mode 1
            ("seg_a50", _unit_axis(50, noise=0.05)),  # per_a mode 2
            ("seg_b", _unit_axis(1, noise=0.05)),     # per_b
            ("seg_q50", _unit_axis(50, noise=0.10)),  # query near per_a's second mode
        ],
    )
    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_a0", "seg_a50"])
    label_segments_as_person(config=config, person_id="per_b", segment_ids=["seg_b"])

    auto_attribute_enrolled(config=config, threshold=0.5)

    overrides = _override_rows(config.database_path)
    assert overrides["seg_q50"]["person_id"] == "per_a"


def test_knn_no_labeled_segments_raises(tmp_path: Path) -> None:
    # Enrolled centroids alone are not enough — kNN needs labeled (manual) segments.
    config = _setup_two_clusters(tmp_path)
    enroll_person(config=config, person_id="per_a", segment_ids=["seg_1", "seg_2", "seg_3"])
    try:
        auto_attribute_enrolled(config=config, threshold=0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when no labeled segments")


def test_knn_non_speaker_class_classifies_noise(tmp_path: Path) -> None:
    # A non_speaker person is a real labeled class for kNN: a segment near labeled-noise gets
    # classified as that noise person.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_a", "seg_n", "seg_qn"])
    _insert_persons(config.database_path, {"per_a": "Alice"})
    noise_id = ensure_person(config=config, display_name="噪音/多人", person_type="non_speaker")
    put_embeddings_bulk(
        config=config,
        items=[
            ("seg_a", _unit_axis(0, noise=0.05)),
            ("seg_n", _unit_axis(70, noise=0.05)),   # labeled noise
            ("seg_qn", _unit_axis(70, noise=0.10)),  # query near labeled noise
        ],
    )
    label_segments_as_person(config=config, person_id="per_a", segment_ids=["seg_a"])
    label_segments_as_person(config=config, person_id=noise_id, segment_ids=["seg_n"])

    auto_attribute_enrolled(config=config, threshold=0.5)

    overrides = _override_rows(config.database_path)
    assert overrides["seg_qn"]["person_id"] == noise_id


def test_ensure_person_idempotent(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    first = ensure_person(config=config, display_name="噪音/多人", person_type="non_speaker")
    second = ensure_person(config=config, display_name="噪音/多人", person_type="non_speaker")
    assert first == second
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select person_type from persons where person_id = ?", (first,))
    finally:
        conn.close()
    assert rows == [{"person_type": "non_speaker"}]


def test_suggest_excludes_non_speaker(tmp_path: Path) -> None:
    # suggest_people_for_session must not suggest a non_speaker person as a cluster's identity.
    config = _setup_two_clusters(tmp_path)
    enroll_person(config=config, person_id="per_a", segment_ids=["seg_1", "seg_2", "seg_3"])
    enroll_person(config=config, person_id="per_b", segment_ids=["seg_4", "seg_5", "seg_6"])
    # Enroll a non_speaker person whose centroid sits near e1 (where per_b's query cluster is).
    noise_id = ensure_person(config=config, display_name="噪音/多人", person_type="non_speaker")
    enroll_person(config=config, person_id=noise_id, segment_ids=["seg_4", "seg_5", "seg_6"])

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
    suggested_ids = {s["person_id"] for s in result["suggestions"]}
    assert noise_id not in suggested_ids
    by_speaker = {s["speaker"]: s for s in result["suggestions"]}
    assert by_speaker["spk_qa"]["person_id"] == "per_a"
    assert by_speaker["spk_qb"]["person_id"] == "per_b"


# ---------------------------------------------------------------------------
# Item 2: multi-scope, tunable projection (project_embeddings)
# ---------------------------------------------------------------------------


def _embed_axis_session(
    config: AppConfig,
    *,
    session_id: str,
    audio_file_id: str,
    segment_ids: list[str],
    axis: int,
    date_key: str = "2087-05-10",
) -> None:
    """Insert a session + segments and embed each near a given axis (distinct cluster per session)."""
    _insert_session_with_segments(
        config.database_path, segment_ids, session_id=session_id, audio_file_id=audio_file_id, date_key=date_key
    )
    put_embeddings_bulk(
        config=config,
        items=[(sid, _unit_axis(axis, noise=0.05 + 0.05 * i)) for i, sid in enumerate(segment_ids)],
    )


def test_project_embeddings_multi_session_spans_both(tmp_path: Path) -> None:
    clear_projection_cache()
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _embed_axis_session(config, session_id="ses_1", audio_file_id="aud_1", segment_ids=["s1a", "s1b", "s1c"], axis=0)
    _embed_axis_session(config, session_id="ses_2", audio_file_id="aud_2", segment_ids=["s2a", "s2b", "s2c"], axis=1)

    result = project_embeddings(config=config, session_ids=["ses_1", "ses_2"], method="pca")

    assert result["method"] == "pca"
    assert result["n"] == 6
    assert result["capped"] is False
    assert result["total_in_scope"] == 6
    by_id = {p["segment_id"]: p for p in result["points"]}
    assert set(by_id) == {"s1a", "s1b", "s1c", "s2a", "s2b", "s2c"}
    # Each point carries its originating session_id so the UI can color/compare by session.
    for sid in ("s1a", "s1b", "s1c"):
        assert by_id[sid]["session_id"] == "ses_1"
    for sid in ("s2a", "s2b", "s2c"):
        assert by_id[sid]["session_id"] == "ses_2"
    for point in result["points"]:
        assert 0.0 <= point["x"] <= 1.0
        assert 0.0 <= point["y"] <= 1.0


def test_project_embeddings_days_scope(tmp_path: Path) -> None:
    clear_projection_cache()
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _embed_axis_session(
        config, session_id="ses_d1", audio_file_id="aud_d1", segment_ids=["d1a", "d1b"], axis=0, date_key="2087-05-10"
    )
    _embed_axis_session(
        config, session_id="ses_d2", audio_file_id="aud_d2", segment_ids=["d2a", "d2b"], axis=1, date_key="2087-05-11"
    )

    result = project_embeddings(config=config, days=["2087-05-10"], method="pca")
    assert {p["segment_id"] for p in result["points"]} == {"d1a", "d1b"}
    assert result["n"] == 2


def test_project_embeddings_pca_component_selection(tmp_path: Path) -> None:
    clear_projection_cache()
    config = _setup_two_clusters(tmp_path)  # seg_1..6, dim 192

    xy01 = project_embeddings(config=config, session_ids=["ses_test"], method="pca", pca_x=0, pca_y=1)
    xy02 = project_embeddings(config=config, session_ids=["ses_test"], method="pca", pca_x=0, pca_y=2)
    a = {p["segment_id"]: (p["x"], p["y"]) for p in xy01["points"]}
    b = {p["segment_id"]: (p["x"], p["y"]) for p in xy02["points"]}
    # Different y-component selection -> the y coords differ (deterministic, not identical).
    assert any(a[s][1] != b[s][1] for s in a)


def test_project_embeddings_pca_out_of_range_y_clamped(tmp_path: Path) -> None:
    clear_projection_cache()
    config = _setup_two_clusters(tmp_path)
    # pca_y far beyond available components must not crash; it's clamped to a valid axis.
    result = project_embeddings(config=config, session_ids=["ses_test"], method="pca", pca_x=0, pca_y=9999)
    assert result["n"] == 6
    for point in result["points"]:
        assert 0.0 <= point["x"] <= 1.0
        assert 0.0 <= point["y"] <= 1.0


def test_project_embeddings_caps_evenly(tmp_path: Path) -> None:
    clear_projection_cache()
    config = _setup_two_clusters(tmp_path)  # 6 segments

    result = project_embeddings(config=config, session_ids=["ses_test"], method="pca", max_points=3)
    assert result["capped"] is True
    assert result["n"] == 3
    assert result["total_in_scope"] == 6


def test_project_embeddings_umap_and_tsne_shapes(tmp_path: Path) -> None:
    clear_projection_cache()
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    # >=10 points so both umap (>=5) and tsne (>=10) run their real reducers.
    ids = [f"seg_{i:02d}" for i in range(12)]
    _insert_session_with_segments(config.database_path, ids)
    put_embeddings_bulk(
        config=config,
        items=[(sid, _unit_axis(0 if i < 6 else 1, noise=0.05 + 0.02 * i)) for i, sid in enumerate(ids)],
    )

    for method in ("umap", "tsne"):
        clear_projection_cache()
        result = project_embeddings(config=config, session_ids=["ses_test"], method=method)
        assert result["method"] == method
        assert result["n"] == 12
        for point in result["points"]:
            assert 0.0 <= point["x"] <= 1.0
            assert 0.0 <= point["y"] <= 1.0


def test_project_embeddings_umap_tsne_fall_back_to_pca_when_small(tmp_path: Path) -> None:
    clear_projection_cache()
    config = _setup_two_clusters(tmp_path)  # 6 segments: enough for umap (>=5) but not tsne (>=10)

    # tsne needs >=10 -> pca fallback.
    tsne = project_embeddings(config=config, session_ids=["ses_test"], method="tsne")
    assert tsne["method"] == "pca"
    assert tsne["n"] == 6

    # umap needs >=5 points; with only 3 it falls back to pca.
    clear_projection_cache()
    small = project_embeddings(config=config, session_ids=["ses_test"], method="umap", max_points=3)
    assert small["method"] == "pca"
    assert small["n"] == 3


def test_project_embeddings_cache_and_clear(tmp_path: Path) -> None:
    clear_projection_cache()
    config = _setup_two_clusters(tmp_path)

    first = project_embeddings(config=config, session_ids=["ses_test"], method="pca")
    second = project_embeddings(config=config, session_ids=["ses_test"], method="pca")
    assert second is first  # cached: same object

    clear_projection_cache()
    third = project_embeddings(config=config, session_ids=["ses_test"], method="pca")
    assert third is not first
    assert third == first


def test_project_embeddings_empty_scope(tmp_path: Path) -> None:
    clear_projection_cache()
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    result = project_embeddings(config=config, session_ids=["ses_missing"], method="umap")
    assert result == {"points": [], "method": "umap", "n": 0, "capped": False}


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


def _insert_segments_with_speakers(database_path: Path, rows: list[tuple[str, str]]) -> None:
    """rows = [(segment_id, speaker)] — speaker_cluster_id starts equal to speaker (per-file label)."""
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_c", "DJI Mic 3", "/source/aud_c.wav", 1, 1, "/raw/aud_c.wav", "sha256:aud_c", 2000, "2026-06-09T08:00:00+08:00", "2026-06-09T08:00:00+08:00", "imported"),
        )
        for index, (segment_id, speaker) in enumerate(rows):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (segment_id, "aud_c", f"chk_{segment_id}", index * 1000, (index + 1) * 1000, "t", "zh", speaker, speaker, f"ev_{segment_id}", "2026-06-09T08:00:00+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def _cluster_ids(database_path: Path) -> dict[str, str]:
    conn = connect(database_path)
    try:
        rows = fetch_all(conn, "select segment_id, speaker, speaker_cluster_id from transcript_segments")
    finally:
        conn.close()
    return {str(r["segment_id"]): (str(r["speaker"]), str(r["speaker_cluster_id"])) for r in rows}


def test_cluster_voiceprints_groups_by_voice_and_preserves_self(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    # Two voices (axis 0 / axis 1) under colliding per-file labels (spk_01 reused), plus self.
    a = [(f"a{i}", "spk_01") for i in range(8)]
    b = [(f"b{i}", "spk_02") for i in range(8)]
    s = [(f"s{i}", "self") for i in range(3)]
    _insert_segments_with_speakers(config.database_path, a + b + s)
    put_embeddings_bulk(
        config=config,
        items=[(sid, _unit_axis(0, noise=0.05 + 0.02 * i)) for i, (sid, _) in enumerate(a)]
        + [(sid, _unit_axis(1, noise=0.05 + 0.02 * i)) for i, (sid, _) in enumerate(b)]
        + [(sid, _unit_axis(0, noise=0.05)) for sid, _ in s],  # self embeddings exist but are skipped
    )

    result = cluster_voiceprints(config=config, min_cluster_size=5)

    assert result["clusters"] == 2
    assert result["scope_segments"] == 16  # self excluded
    rows = _cluster_ids(config.database_path)
    # self untouched (still 'self'), original per-file speaker preserved for all.
    for sid, _ in s:
        assert rows[sid] == ("self", "self")
    # Each voice group collapsed to ONE vp cluster, and the two groups differ.
    a_clusters = {rows[sid][1] for sid, _ in a}
    b_clusters = {rows[sid][1] for sid, _ in b}
    assert len(a_clusters) == 1 and len(b_clusters) == 1
    assert a_clusters != b_clusters
    assert next(iter(a_clusters)).startswith("vp_")
    # Original per-file label preserved (reversible).
    assert rows["a0"][0] == "spk_01"


def _insert_person_row(database_path: Path, *, person_id: str, name: str, ptype: str) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, 0, ?, ?)",
            (person_id, name, ptype, "2026-06-09T00:00:00+08:00", "2026-06-09T00:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_seg(database_path: Path, *, seg_id: str, text: str, dur_ms: int) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert or ignore into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('aud_n','d','/s','/r','sha',1,'2026-06-09T08:00:00+08:00','2026-06-09T08:00:00+08:00','imported')",
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, is_active, created_at) values (?, 'aud_n', ?, 0, ?, ?, 'zh', 'spk_01', 'spk_01', ?, 1, '2026-06-09T08:00:00+08:00')",
            (seg_id, f"chk_{seg_id}", dur_ms, text, f"ev_{seg_id}"),
        )
        conn.commit()
    finally:
        conn.close()


def test_mark_noise_filler_and_short_preserves_manual_labels(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_row(config.database_path, person_id="per_noise", name="噪音/多人", ptype="non_speaker")
    _insert_person_row(config.database_path, person_id="per_alice", name="Alice", ptype="contact")
    _insert_seg(config.database_path, seg_id="s_fill", text="嗯嗯，", dur_ms=1500)   # filler -> noise
    _insert_seg(config.database_path, seg_id="s_short", text="有意义", dur_ms=200)    # short -> noise
    _insert_seg(config.database_path, seg_id="s_norm", text="这是一句正常的长话", dur_ms=3000)  # untouched
    _insert_seg(config.database_path, seg_id="s_fill_real", text="啊", dur_ms=1500)  # filler BUT manually labeled
    # s_fill_real is already a manual label to a real person -> must be preserved.
    label_segments_as_person(config=config, person_id="per_alice", segment_ids=["s_fill_real"]) if False else None
    conn = connect(config.database_path)
    conn.execute(
        "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values ('s_fill_real','Alice','2026-06-09T08:00:00+08:00','per_alice','manual')"
    )
    conn.commit(); conn.close()

    result = mark_noise_segments(config=config, noise_person_id="per_noise", filler=True, max_duration_ms=300)

    assert result["marked"] == 2
    overrides = _override_rows(config.database_path)
    assert overrides["s_fill"]["person_id"] == "per_noise"
    assert overrides["s_short"]["person_id"] == "per_noise"
    assert "s_norm" not in overrides  # normal long segment untouched
    assert overrides["s_fill_real"]["person_id"] == "per_alice"  # manual label preserved


def test_mark_noise_requires_a_criterion(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_row(config.database_path, person_id="per_noise", name="噪音", ptype="non_speaker")
    try:
        mark_noise_segments(config=config, noise_person_id="per_noise")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_global_clusters_lists_vp_sorted_with_dominant_person(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_row(config.database_path, person_id="per_a", name="Alice", ptype="contact")
    # vp_001 (3 segs) > vp_002 (2 segs); a self seg must be excluded (not vp_*).
    _insert_segments_with_speakers(
        config.database_path,
        [("c1", "vp_001"), ("c2", "vp_001"), ("c3", "vp_001"), ("d1", "vp_002"), ("d2", "vp_002"), ("self1", "self")],
    )
    # Label 2 of vp_001's 3 segments as Alice -> dominant person for vp_001.
    conn = connect(config.database_path)
    for sid in ("c1", "c2"):
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values (?, 'Alice', '2026-06-09T00:00:00+08:00', 'per_a', 'manual')",
            (sid,),
        )
    conn.commit(); conn.close()

    clusters = global_clusters(config=config)
    ids = [c["speaker_cluster_id"] for c in clusters]
    assert ids == ["vp_001", "vp_002"]  # largest first, self excluded
    vp1 = clusters[0]
    assert vp1["segment_count"] == 3
    assert vp1["person_id"] == "per_a" and vp1["person_label"] == "Alice" and vp1["labeled_count"] == 2
    assert vp1["sample_text"] is not None
    assert clusters[1]["person_id"] is None  # vp_002 unassigned


def test_global_clusters_returns_multiple_sample_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    rows = [(f"c{i}", "vp_001") for i in range(1, 7)] + [("d1", "vp_002")]
    _insert_segments_with_speakers(config.database_path, rows)

    conn = connect(config.database_path)
    try:
        updates = [
            ("c1", 0, 900, "短句"),
            ("c2", 1000, 2500, "这是一条最长的代表样例"),
            ("c3", 3000, 4200, "第二条语气样例"),
            ("c4", 4300, 5200, "第三条决策样例"),
            ("c5", 5300, 5900, "第四条补充样例"),
            ("c6", 6000, 6400, "第五条不应返回"),
        ]
        for segment_id, start_ms, end_ms, text in updates:
            conn.execute(
                "update transcript_segments set start_ms = ?, end_ms = ?, text = ? where segment_id = ?",
                (start_ms, end_ms, text, segment_id),
            )
        conn.commit()
    finally:
        conn.close()

    vp1 = next(c for c in global_clusters(config=config) if c["speaker_cluster_id"] == "vp_001")

    assert vp1["sample_text"] == "这是一条最长的代表样例"
    assert vp1["sample_segments"] == [
        {"segment_id": "c2", "text": "这是一条最长的代表样例"},
        {"segment_id": "c3", "text": "第二条语气样例"},
        {"segment_id": "c1", "text": "短句"},
        {"segment_id": "c4", "text": "第三条决策样例"},
    ]


def test_assign_cluster_to_person_labels_every_segment(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_row(config.database_path, person_id="per_b", name="Bob", ptype="contact")
    _insert_segments_with_speakers(config.database_path, [("x1", "vp_007"), ("x2", "vp_007"), ("x3", "vp_007")])

    result = assign_cluster_to_person(config=config, cluster_id="vp_007", person_id="per_b")
    assert result["labeled"] == 3
    overrides = _override_rows(config.database_path)
    assert {overrides[s]["person_id"] for s in ("x1", "x2", "x3")} == {"per_b"}


def test_assign_cluster_unknown_is_empty(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_row(config.database_path, person_id="per_b", name="Bob", ptype="contact")
    result = assign_cluster_to_person(config=config, cluster_id="vp_nope", person_id="per_b")
    assert result["labeled"] == 0


def test_identification_status_counts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_row(config.database_path, person_id="per_a", name="Alice", ptype="contact")
    _insert_segments_with_speakers(config.database_path, [("g1", "vp_001"), ("g2", "vp_001"), ("g3", "vp_002"), ("g4", "self")])
    put_embeddings_bulk(config=config, items=[("g1", [1.0, 0.0]), ("g2", [1.0, 0.0]), ("g3", [0.0, 1.0])])  # 3 of 4 embedded
    conn = connect(config.database_path)
    for sid in ("g1", "g2"):
        conn.execute(
            "insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values (?, 'Alice', '2026-06-09T00:00:00+08:00', 'per_a', 'manual')",
            (sid,),
        )
    conn.commit(); conn.close()

    st = identification_status(config=config)
    assert st["total"] == 4
    assert st["embedded"] == 3
    assert st["clusters"] == 2  # vp_001, vp_002 (self is not vp_*)
    assert st["identified"] == 2
    assert st["unidentified"] == 2


# ---------------------------------------------------------------------------
# Zero-padding BATCHED extraction (embed_batch_fn / _bucket_by_duration).


def test_bucket_by_duration_groups_only_equal_frame_counts() -> None:
    # ONLY exactly-equal fbank frame counts may share a bucket: any zero padding measurably
    # corrupts the CAM++ voiceprint (even <=1.25x duration ratios degraded real production
    # cosines to 0.71), while equal-frame batches are bit-identical to solo inference.
    from personal_context_node.speaker_embeddings import _bucket_by_duration, _fbank_frame_key

    # frames = 1 + (ms - 25) // 10: 1000..1004ms -> 98 frames; 1005ms -> 99.
    assert _fbank_frame_key(1000) == _fbank_frame_key(1004) == 98
    assert _fbank_frame_key(1005) == 99
    assert _fbank_frame_key(24) < 0  # sub-window clips key on negative exact ms

    items = [("a", "pa", 1000), ("b", "pb", 1004), ("c", "pc", 1005), ("d", "pd", 5000)]
    buckets = _bucket_by_duration(items, max_batch_size=32)
    assert buckets == [
        [("a", "pa"), ("b", "pb")],  # same 98-frame count -> zero-padding-free batch
        [("c", "pc")],  # 99 frames -> its own (solo-equivalent) bucket
        [("d", "pd")],
    ]

    same = [(f"s{i}", f"p{i}", 2000) for i in range(40)]
    assert [len(b) for b in _bucket_by_duration(same, max_batch_size=32)] == [32, 8]

    assert _bucket_by_duration([]) == []
    assert _bucket_by_duration([("x", "px", 0)]) == [[("x", "px")]]
    # Two sub-window clips of the SAME exact length may batch; a different length may not.
    assert _bucket_by_duration([("x", "px", 20), ("y", "py", 20), ("z", "pz", 21)]) == [
        [("z", "pz")],
        [("x", "px"), ("y", "py")],
    ]


def _fail_serial(path: str) -> list[float]:
    raise AssertionError("serial embed_fn must not be called on the batched path")


def test_extract_pending_embeddings_batched_path(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    # seg_2 has no resolvable audio -> skipped (kept pending); the rest share one duration bucket.
    monkeypatch.setattr(
        transcription,
        "bulk_segment_audio_info",
        lambda *, config, segment_ids: {
            sid: (Path(f"/slices/{sid}.wav"), 1000) for sid in segment_ids if sid != "seg_2"
        },
    )
    calls: list[list[tuple[str, str]]] = []

    def embed_batch_fn(items):
        calls.append(list(items))
        return [{"segment_id": sid, "embedding": [0.1, 0.2, 0.3]} for sid, _ in items]

    ticks: list[tuple[int, int]] = []
    result = extract_pending_embeddings(
        config=config, embed_fn=_fail_serial, embed_batch_fn=embed_batch_fn,
        progress=lambda done, total: ticks.append((done, total)),
    )

    assert result == {"embedded": 2, "skipped_missing_audio": 1, "failed": 0, "total": 3}
    assert len(calls) == 1  # equal durations -> ONE bucket, one wire round-trip
    assert [sid for sid, _ in calls[0]] == ["seg_1", "seg_3"]
    assert set(get_embeddings(config=config, segment_ids=["seg_1", "seg_3"])) == {"seg_1", "seg_3"}
    assert pending_embedding_segment_ids(config=config) == ["seg_2"]  # skipped stays pending
    assert ticks[-1] == (3, 3)


def test_extract_pending_embeddings_batched_error_isolation(tmp_path: Path, monkeypatch) -> None:
    # Bucket A (seg_1+seg_2, ~1s): per-item error entry fails ONLY that item. Bucket B (seg_3,
    # 10s): the whole wire call raising fails only that bucket. The pass never aborts.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2", "seg_3"])

    durations = {"seg_1": 1000, "seg_2": 1004, "seg_3": 10_000}  # seg_1+seg_2 share a frame count
    monkeypatch.setattr(
        transcription,
        "bulk_segment_audio_info",
        lambda *, config, segment_ids: {
            sid: (Path(f"/slices/{sid}.wav"), durations[sid]) for sid in segment_ids
        },
    )

    def embed_batch_fn(items):
        ids = [sid for sid, _ in items]
        if "seg_3" in ids:
            raise RuntimeError("wire timeout")
        return [
            {"segment_id": "seg_1", "embedding": [1.0, 2.0]},
            {"segment_id": "seg_2", "error": "bad wav"},
        ]

    result = extract_pending_embeddings(config=config, embed_fn=_fail_serial, embed_batch_fn=embed_batch_fn)

    assert result == {"embedded": 1, "skipped_missing_audio": 0, "failed": 2, "total": 3}
    assert set(get_embeddings(config=config, segment_ids=["seg_1", "seg_2", "seg_3"])) == {"seg_1"}
    assert pending_embedding_segment_ids(config=config) == ["seg_2", "seg_3"]


def test_extract_pending_embeddings_batched_rejects_non_finite(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1"])
    monkeypatch.setattr(
        transcription,
        "bulk_segment_audio_info",
        lambda *, config, segment_ids: {sid: (Path(f"/s/{sid}.wav"), 1000) for sid in segment_ids},
    )
    result = extract_pending_embeddings(
        config=config, embed_fn=_fail_serial,
        embed_batch_fn=lambda items: [{"segment_id": sid, "embedding": [float("nan"), 1.0]} for sid, _ in items],
    )
    assert result == {"embedded": 0, "skipped_missing_audio": 0, "failed": 1, "total": 1}


def test_pending_embedding_segment_ids_audio_file_scope(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_a1", "seg_a2"], session_id="ses_a", audio_file_id="aud_a")
    _insert_session_with_segments(config.database_path, ["seg_b1"], session_id="ses_b", audio_file_id="aud_b")

    assert pending_embedding_segment_ids(config=config, audio_file_id="aud_a") == ["seg_a1", "seg_a2"]
    assert pending_embedding_segment_ids(config=config, audio_file_id="aud_b") == ["seg_b1"]
    assert pending_embedding_segment_ids(config=config, audio_file_id="aud_none") == []


def test_combined_batched_runs_embed_and_emotion_concurrently(tmp_path: Path, monkeypatch) -> None:
    import threading

    from personal_context_node.segment_emotions import get_emotions as _get_emotions

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path, ["seg_1", "seg_2"])
    monkeypatch.setattr(
        transcription,
        "bulk_segment_audio_info",
        lambda *, config, segment_ids: {sid: (Path(f"/s/{sid}.wav"), 1000) for sid in segment_ids},
    )

    # Each pass blocks until the OTHER has started: only a truly concurrent combined run can
    # finish without tripping the 5s timeouts. (A serial implementation deadlocks the first
    # pass's wait and fails the assertion inside the worker thread, which re-raises after join.)
    embed_started = threading.Event()
    emotion_started = threading.Event()

    def embed_batch_fn(items):
        embed_started.set()
        assert emotion_started.wait(5.0), "emotion pass did not run concurrently with embed pass"
        return [{"segment_id": sid, "embedding": [0.5, 0.5]} for sid, _ in items]

    def classify_batch_fn(items):
        emotion_started.set()
        assert embed_started.wait(5.0), "embed pass did not run concurrently with emotion pass"
        return [{"segment_id": sid, "label": "happy", "scores": {"happy": 0.9}} for sid, _ in items]

    ticks: list[tuple[int, int]] = []
    result = extract_pending_embeddings_and_emotions(
        config=config,
        embed_fn=_fail_serial,
        classify_fn=lambda path: (_ for _ in ()).throw(AssertionError("serial classify_fn must not run")),
        embed_batch_fn=embed_batch_fn,
        classify_batch_fn=classify_batch_fn,
        progress=lambda done, total: ticks.append((done, total)),
    )

    assert result["embedding"] == {"embedded": 2, "skipped_missing_audio": 0, "failed": 0, "total": 2}
    assert result["emotion"] == {"emoted": 2, "skipped_missing_audio": 0, "failed": 0, "total": 2}
    assert set(get_embeddings(config=config, segment_ids=["seg_1", "seg_2"])) == {"seg_1", "seg_2"}
    assert set(_get_emotions(config=config, segment_ids=["seg_1", "seg_2"])) == {"seg_1", "seg_2"}
    # Progress ticks once per ARTIFACT operation on the concurrent path: 2 embeds + 2 emotions.
    assert sorted(ticks)[-1] == (4, 4)
