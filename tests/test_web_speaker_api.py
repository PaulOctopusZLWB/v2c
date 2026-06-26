from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.speaker_embeddings import put_embedding
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.web.app import create_app


def test_assign_speaker_to_person(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/speakers/spk_1/assign-person", json={"person_id": "per_paul"})

    assert response.status_code == 200
    assert response.json() == {"speaker": "spk_1", "person_id": "per_paul", "person_label": "Paul"}
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select speaker, person_id, person_label from speaker_mappings")
    finally:
        conn.close()
    assert rows == [{"speaker": "spk_1", "person_id": "per_paul", "person_label": "Paul"}]


def test_assign_unknown_person_returns_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/speakers/spk_1/assign-person", json={"person_id": "ghost"})

    assert response.status_code == 404


def test_segment_person_override(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/transcripts/segments/seg_1/person-override", json={"person_id": "per_paul"})

    assert response.status_code == 200
    assert response.json() == {"segment_id": "seg_1", "person_id": "per_paul", "person_label": "Paul"}


def test_list_persons_includes_seeded_self(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    persons = client.get("/api/persons").json()["persons"]

    assert any(p["person_id"] == "per_paul" and p["is_self"] == 1 for p in persons)


def test_create_person_then_assign(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    created = client.post("/api/persons", json={"display_name": "Mira"})
    assert created.status_code == 200
    new_id = created.json()["person_id"]

    assigned = client.post("/api/speakers/spk_1/assign-person", json={"person_id": new_id})
    assert assigned.status_code == 200
    assert assigned.json() == {"speaker": "spk_1", "person_id": new_id, "person_label": "Mira"}


def test_list_clusters_for_day(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_diarized_day(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/speakers/clusters", params={"day": "2087-05-10"})

    assert response.status_code == 200
    clusters = response.json()["clusters"]
    by_id = {c["speaker_cluster_id"]: c for c in clusters}
    assert set(by_id) == {"spk_01", "spk_02", "spk_03"}

    # spk_01: two segments (300ms + 700ms), longest is seg_01b.
    spk01 = by_id["spk_01"]
    assert spk01["segment_count"] == 2
    assert spk01["total_speech_ms"] == 1000
    assert spk01["sample_segment_id"] == "seg_01b"
    assert spk01["sample_text"] == "spk01 longer sample text"
    assert spk01["person_id"] is None
    assert spk01["person_label"] is None

    # spk_02: one segment of 500ms.
    spk02 = by_id["spk_02"]
    assert spk02["segment_count"] == 1
    assert spk02["total_speech_ms"] == 500
    assert spk02["sample_segment_id"] == "seg_02"
    assert spk02["sample_text"] == "spk02 sample"

    # spk_03: one segment of 200ms.
    spk03 = by_id["spk_03"]
    assert spk03["segment_count"] == 1
    assert spk03["total_speech_ms"] == 200
    assert spk03["sample_segment_id"] == "seg_03"

    # Ordered by segment_count desc: spk_01 (2) first.
    assert clusters[0]["speaker_cluster_id"] == "spk_01"


def test_global_clusters_route_includes_sample_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_diarized_day(config.database_path)
    conn = connect(config.database_path)
    try:
        conn.execute("update transcript_segments set speaker_cluster_id = 'vp_001', speaker = 'vp_001' where speaker_cluster_id = 'spk_01'")
        conn.execute("update transcript_segments set speaker_cluster_id = 'vp_002', speaker = 'vp_002' where speaker_cluster_id = 'spk_02'")
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    response = client.get("/api/speakers/global-clusters")

    assert response.status_code == 200
    clusters = response.json()["clusters"]
    vp1 = next(c for c in clusters if c["speaker_cluster_id"] == "vp_001")
    assert vp1["sample_segments"] == [
        {"segment_id": "seg_01b", "text": "spk01 longer sample text"},
        {"segment_id": "seg_01a", "text": "spk01 short"},
    ]


def test_assign_person_bulk_merges_clusters(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_diarized_day(config.database_path)
    client = TestClient(create_app(config=config))

    created = client.post("/api/persons", json={"display_name": "Mira"})
    assert created.status_code == 200
    person_id = created.json()["person_id"]

    response = client.post(
        "/api/speakers/assign-person-bulk",
        json={"speakers": ["spk_01", "spk_02", "spk_03"], "person_id": person_id},
    )

    assert response.status_code == 200
    assert response.json() == {"assigned": 3}

    # Clusters list now shows all three under the one person.
    clusters = client.get("/api/speakers/clusters", params={"day": "2087-05-10"}).json()["clusters"]
    by_id = {c["speaker_cluster_id"]: c for c in clusters}
    for cluster_id in ("spk_01", "spk_02", "spk_03"):
        assert by_id[cluster_id]["person_id"] == person_id
        assert by_id[cluster_id]["person_label"] == "Mira"

    # The attribution view collapses every cluster's segments to the one person (the merge).
    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            "select distinct person_id from v_segment_attribution where person_id is not null",
        )
    finally:
        conn.close()
    assert rows == [{"person_id": person_id}]


def test_assign_person_bulk_unknown_person_returns_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_diarized_day(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/speakers/assign-person-bulk",
        json={"speakers": ["spk_01"], "person_id": "ghost"},
    )

    assert response.status_code == 404


def test_assign_person_bulk_empty_speakers_returns_400(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_diarized_day(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/speakers/assign-person-bulk",
        json={"speakers": [], "person_id": "per_paul"},
    )

    assert response.status_code == 400


def test_clusters_scoped_by_session_date_key_not_recorded_at(tmp_path: Path) -> None:
    # Cross-midnight: a file recorded late on 2087-05-10 whose session date_key is 2087-05-11. The
    # cluster list must follow the session date_key (the day the UI picker offers), not recorded_at.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_x", "dev", "/x.wav", 1, 1, "/raw/x.wav", "sha:x", 1000, "2087-05-10T23:50:00+08:00", "2087-05-10T23:50:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_x", "2087-05-11", "2087-05-11T00:05:00+08:00", "2087-05-11T00:06:00+08:00", "derived_from_segments", 1, 500, "seg_x", "2087-05-11T00:06:00+08:00", "2087-05-11T00:06:00+08:00"),
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("seg_x", "aud_x", "chk_x", "ses_x", 0, 500, "跨午夜", "zh", "spk_01", "spk_01", "ev_x", 1.0, "mock", "mock", "t", 1, "2087-05-11T00:06:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    by_date_key = client.get("/api/speakers/clusters", params={"day": "2087-05-11"}).json()["clusters"]
    assert [c["speaker_cluster_id"] for c in by_date_key] == ["spk_01"]  # found under the session date_key
    by_recorded_at = client.get("/api/speakers/clusters", params={"day": "2087-05-10"}).json()["clusters"]
    assert by_recorded_at == []  # NOT scoped by the file's recorded_at day


def test_embedding_status_counts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    # Embed exactly one of the three active segments.
    put_embedding(config=config, segment_id="seg_a", vector=[1.0, 0.0, 0.0])
    client = TestClient(create_app(config=config))

    response = client.get("/api/speakers/embedding-status")
    assert response.status_code == 200
    assert response.json() == {"total": 3, "embedded": 1, "pending": 2}

    # Scoping by session_id keeps the same three-segment scope (all share ses_lab).
    scoped = client.get("/api/speakers/embedding-status", params={"session_id": "ses_lab"})
    assert scoped.status_code == 200
    assert scoped.json() == {"total": 3, "embedded": 1, "pending": 2}

    # A session with no segments yields all zeros.
    empty = client.get("/api/speakers/embedding-status", params={"session_id": "ses_missing"})
    assert empty.status_code == 200
    assert empty.json() == {"total": 0, "embedded": 0, "pending": 0}


def test_embedding_projection_route(tmp_path: Path) -> None:
    from personal_context_node.speaker_embeddings import clear_projection_cache

    clear_projection_cache()
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    # Embed all three active segments: two near +x, one near +y (separable for PCA).
    put_embedding(config=config, segment_id="seg_a", vector=[1.0, 0.0, 0.0])
    put_embedding(config=config, segment_id="seg_b", vector=[0.9, 0.1, 0.0])
    put_embedding(config=config, segment_id="seg_c", vector=[0.0, 1.0, 0.0])
    client = TestClient(create_app(config=config))

    response = client.get(
        "/api/speakers/embedding-projection", params={"session_id": "ses_lab", "method": "pca"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "pca"
    assert body["n"] == 3
    ids = {p["segment_id"] for p in body["points"]}
    assert ids == {"seg_a", "seg_b", "seg_c"}
    for point in body["points"]:
        assert 0.0 <= point["x"] <= 1.0
        assert 0.0 <= point["y"] <= 1.0
        assert "speaker" in point

    # Empty scope still 200 with an empty points list.
    empty = client.get(
        "/api/speakers/embedding-projection", params={"session_id": "ses_missing", "method": "pca"}
    )
    assert empty.status_code == 200
    assert empty.json() == {"points": [], "method": "pca", "n": 0}

    # Unknown method -> 400.
    bad = client.get(
        "/api/speakers/embedding-projection", params={"session_id": "ses_lab", "method": "tsne"}
    )
    assert bad.status_code == 400


def test_projection_route_multi_scope(tmp_path: Path) -> None:
    from personal_context_node.speaker_embeddings import clear_projection_cache, put_embeddings_bulk

    clear_projection_cache()
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    # 192-dim vectors near two distinct axes so PCA is non-degenerate.
    import numpy as np

    def axis(i: int) -> list[float]:
        v = np.zeros(192, dtype=np.float64)
        v[i] = 1.0
        v[(i + 1) % 192] = 0.1
        return v.tolist()

    put_embeddings_bulk(
        config=config,
        items=[("seg_a", axis(0)), ("seg_b", axis(0)), ("seg_c", axis(1))],
    )
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/speakers/projection", json={"session_ids": ["ses_lab"], "method": "pca"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "pca"
    assert body["n"] == 3
    ids = {p["segment_id"] for p in body["points"]}
    assert ids == {"seg_a", "seg_b", "seg_c"}
    for point in body["points"]:
        assert point["session_id"] == "ses_lab"
        assert 0.0 <= point["x"] <= 1.0
        assert 0.0 <= point["y"] <= 1.0

    # Bad method -> 400.
    bad = client.post("/api/speakers/projection", json={"session_ids": ["ses_lab"], "method": "bogus"})
    assert bad.status_code == 400


def test_recluster_route_returns_distribution(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    # Two persons; hand-made embeddings forming two well-separated clusters.
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_alice", "Alice", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_bob", "Bob", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    # seg_a near +x (Alice), seg_b near +x, seg_c near +y (Bob).
    put_embedding(config=config, segment_id="seg_a", vector=[1.0, 0.0, 0.0])
    put_embedding(config=config, segment_id="seg_b", vector=[0.9, 0.1, 0.0])
    put_embedding(config=config, segment_id="seg_c", vector=[0.0, 1.0, 0.0])
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/speakers/recluster",
        json={"anchors": {"seg_a": "per_alice", "seg_c": "per_bob"}, "threshold": 0.5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["assigned"] == 3
    assert body["per_person"] == {"per_alice": 2, "per_bob": 1}
    assert body["threshold"] == 0.5

    # Bad threshold (out of [0, 1]) -> 400.
    bad = client.post(
        "/api/speakers/recluster",
        json={"anchors": {"seg_a": "per_alice"}, "threshold": 1.5},
    )
    assert bad.status_code == 400

    # Empty anchors -> 400.
    empty = client.post(
        "/api/speakers/recluster",
        json={"anchors": {}, "threshold": 0.5},
    )
    assert empty.status_code == 400


def test_segments_for_labeling(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    put_embedding(config=config, segment_id="seg_a", vector=[1.0, 0.0, 0.0])
    client = TestClient(create_app(config=config))

    response = client.get("/api/speakers/segments", params={"session_id": "ses_lab"})
    assert response.status_code == 200
    segments = response.json()["segments"]
    # Ordered by absolute_start_at, segment_id: seg_a, seg_b, seg_c.
    assert [s["segment_id"] for s in segments] == ["seg_a", "seg_b", "seg_c"]
    by_id = {s["segment_id"]: s for s in segments}
    assert by_id["seg_a"]["has_embedding"] is True
    assert by_id["seg_b"]["has_embedding"] is False
    assert by_id["seg_a"]["text"] == "alice one"
    assert by_id["seg_a"]["speaker"] == "spk_a"

    # Speaker filter.
    filtered = client.get(
        "/api/speakers/segments", params={"session_id": "ses_lab", "speaker": "spk_a"}
    ).json()["segments"]
    assert [s["segment_id"] for s in filtered] == ["seg_a", "seg_b"]

    # Limit respected.
    limited = client.get(
        "/api/speakers/segments", params={"session_id": "ses_lab", "limit": 2}
    ).json()["segments"]
    assert [s["segment_id"] for s in limited] == ["seg_a", "seg_b"]


class _StubEmbedAdapter:
    """Stand-in for PersistentCommandEmbedAdapter: returns a fixed vector, records close()."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.closed = False

    def embed(self, audio_path: str) -> list[float]:
        return list(self._vector)

    def close(self) -> None:
        self.closed = True


def _wait_for(predicate, timeout: float = 5.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def test_extract_embeddings_starts(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    app = create_app(config=config)
    client = TestClient(app)

    # DI seam: inject a stub embed adapter + a stub segment audio path so NO real model and NO real
    # audio slice is needed. segment_audio_path is imported lazily inside extract_pending_embeddings,
    # so patch it at its definition module.
    stub = _StubEmbedAdapter([0.1, 0.1, 0.1, 0.1])
    app.state.worker._embed_factory = lambda: stub
    monkeypatch.setattr(
        "personal_context_node.transcription.segment_audio_path",
        lambda *, config, segment_id: tmp_path / f"{segment_id}.wav",
    )

    before = client.get("/api/speakers/embedding-status").json()
    assert before["pending"] == 3

    started = client.post("/api/speakers/extract-embeddings", json={})
    assert started.status_code == 200
    assert started.json() == {"started": True}

    # Background thread runs to completion: pending drops to 0 and the adapter was closed.
    _wait_for(lambda: not app.state.worker.is_running())
    after = client.get("/api/speakers/embedding-status").json()
    assert after["pending"] == 0
    assert after["embedded"] == 3
    assert stub.closed is True


def test_extract_embeddings_returns_false_when_running(tmp_path: Path, monkeypatch) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    app = create_app(config=config)
    worker = app.state.worker

    # A factory whose adapter blocks until released, so the first run is still "running".
    import threading

    release = threading.Event()

    class _BlockingAdapter:
        def __init__(self) -> None:
            self.closed = False

        def embed(self, audio_path: str) -> list[float]:
            release.wait(5.0)
            return [0.0, 0.0]

        def close(self) -> None:
            self.closed = True

    blocking = _BlockingAdapter()
    worker._embed_factory = lambda: blocking
    monkeypatch.setattr(
        "personal_context_node.transcription.segment_audio_path",
        lambda *, config, segment_id: tmp_path / f"{segment_id}.wav",
    )

    assert worker.start_embedding_extraction() is True
    _wait_for(lambda: worker.embedding_state() is not None and worker.embedding_state().get("active"))
    # Second call while the first is still running is rejected.
    assert worker.start_embedding_extraction() is False
    release.set()
    _wait_for(lambda: not worker.is_running())
    assert blocking.closed is True


# ---------------------------------------------------------------------------
# Slice 5a routes: label-segments / enroll / people / suggest / auto-attribute
# ---------------------------------------------------------------------------


def _enroll_two_clusters(config: AppConfig) -> None:
    """Seed two persons + two enrolled voiceprints over the labeling day's segments.

    seg_a/seg_b near +x -> Alice; seg_c near +y -> Bob. Uses the public enroll path.
    """
    from personal_context_node.speaker_embeddings import enroll_person

    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_alice", "Alice", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_bob", "Bob", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    put_embedding(config=config, segment_id="seg_a", vector=[1.0, 0.0, 0.0])
    put_embedding(config=config, segment_id="seg_b", vector=[0.9, 0.1, 0.0])
    put_embedding(config=config, segment_id="seg_c", vector=[0.0, 1.0, 0.0])
    enroll_person(config=config, person_id="per_alice", segment_ids=["seg_a", "seg_b"])
    enroll_person(config=config, person_id="per_bob", segment_ids=["seg_c"])


def test_label_segments_route(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/people/per_paul/label-segments", json={"segment_ids": ["seg_1"]})
    assert response.status_code == 200
    assert response.json() == {"labeled": 1}

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select segment_id, person_id, person_label from segment_person_overrides")
    finally:
        conn.close()
    assert rows == [{"segment_id": "seg_1", "person_id": "per_paul", "person_label": "Paul"}]


def test_label_segments_unknown_person_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/people/ghost/label-segments", json={"segment_ids": ["seg_1"]})
    assert response.status_code == 404


def test_label_segments_empty_400(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/people/per_paul/label-segments", json={"segment_ids": []})
    assert response.status_code == 400


def test_enroll_person_route(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_alice", "Alice", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    put_embedding(config=config, segment_id="seg_a", vector=[1.0, 0.0, 0.0])
    put_embedding(config=config, segment_id="seg_b", vector=[0.9, 0.1, 0.0])
    client = TestClient(create_app(config=config))

    response = client.post("/api/people/per_alice/enroll", json={"segment_ids": ["seg_a", "seg_b"]})
    assert response.status_code == 200
    body = response.json()
    assert body["person_id"] == "per_alice"
    assert body["n_segments"] == 2
    assert body["dim"] == 3


def test_enroll_person_no_embeddings_400(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_alice", "Alice", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    # No embeddings stored / no attributed segments -> ValueError -> 400.
    response = client.post("/api/people/per_alice/enroll", json={})
    assert response.status_code == 400


def test_people_route_lists_enrichment(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _enroll_two_clusters(config)
    # Attribute two segments to Alice via overrides (attributed_count).
    from personal_context_node.speaker_embeddings import label_segments_as_person

    label_segments_as_person(config=config, person_id="per_alice", segment_ids=["seg_a", "seg_b"])
    client = TestClient(create_app(config=config))

    response = client.get("/api/people")
    assert response.status_code == 200
    people = {p["person_id"]: p for p in response.json()["people"]}
    assert people["per_alice"]["display_name"] == "Alice"
    assert people["per_alice"]["enrolled"] is True
    assert people["per_alice"]["attributed_count"] == 2
    assert people["per_bob"]["enrolled"] is True
    assert people["per_bob"]["attributed_count"] == 0
    assert "is_self" in people["per_alice"]


def test_suggest_route(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _enroll_two_clusters(config)
    client = TestClient(create_app(config=config))

    # The labeling day session ses_lab has spk_a (near +x) and spk_b (near +y).
    response = client.post("/api/speakers/suggest", json={"session_id": "ses_lab"})
    assert response.status_code == 200
    by_speaker = {s["speaker"]: s for s in response.json()["suggestions"]}
    assert by_speaker["spk_a"]["person_id"] == "per_alice"
    assert by_speaker["spk_b"]["person_id"] == "per_bob"


def test_auto_attribute_route(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _enroll_two_clusters(config)
    client = TestClient(create_app(config=config))

    # kNN needs labeled (manual) segments: one seed per person.
    assert client.post("/api/people/per_alice/label-segments", json={"segment_ids": ["seg_a"]}).status_code == 200
    assert client.post("/api/people/per_bob/label-segments", json={"segment_ids": ["seg_c"]}).status_code == 200

    response = client.post("/api/people/auto-attribute", json={"session_id": "ses_lab", "threshold": 0.5})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    # seg_b is the only unlabeled segment; it goes to Alice by nearest-labels vote.
    assert body["assigned"] == 1
    assert body["per_person"] == {"per_alice": 1, "per_bob": 0}
    assert body["threshold"] == 0.5


def test_auto_attribute_no_enrolled_400(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    client = TestClient(create_app(config=config))

    # No labeled segments -> ValueError -> 400.
    response = client.post("/api/people/auto-attribute", json={"session_id": "ses_lab"})
    assert response.status_code == 400


def test_create_person_with_non_speaker_type(tmp_path: Path) -> None:
    # POST /api/persons accepts an optional person_type; default stays 'contact'.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    created = client.post("/api/persons", json={"display_name": "噪音/多人", "person_type": "non_speaker"})
    assert created.status_code == 200
    assert created.json()["person_type"] == "non_speaker"

    # Default person_type is 'contact' when omitted.
    default = client.post("/api/persons", json={"display_name": "王芳"})
    assert default.status_code == 200
    assert default.json()["person_type"] == "contact"


def test_people_route_includes_person_type(tmp_path: Path) -> None:
    # GET /api/people exposes person_type so the frontend can render non_speaker specially.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _enroll_two_clusters(config)
    client = TestClient(create_app(config=config))
    client.post("/api/persons", json={"display_name": "噪音/多人", "person_type": "non_speaker"})

    people = {p["display_name"]: p for p in client.get("/api/people").json()["people"]}
    assert people["Alice"]["person_type"] == "contact"
    assert people["噪音/多人"]["person_type"] == "non_speaker"


def test_non_speaker_labeled_segment_classified_by_identify(tmp_path: Path) -> None:
    # Labeling a segment to a non_speaker person and running identify classifies a near-noise
    # segment as that person (non_speaker is a real kNN class).
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_alice", "Alice", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    # seg_a near +x (Alice); seg_b/seg_c near +y -> labeled noise + a near-noise query.
    put_embedding(config=config, segment_id="seg_a", vector=[1.0, 0.0, 0.0])
    put_embedding(config=config, segment_id="seg_b", vector=[0.0, 1.0, 0.0])
    put_embedding(config=config, segment_id="seg_c", vector=[0.0, 0.95, 0.05])
    client = TestClient(create_app(config=config))

    noise = client.post("/api/persons", json={"display_name": "噪音/多人", "person_type": "non_speaker"})
    noise_id = noise.json()["person_id"]
    assert client.post("/api/people/per_alice/label-segments", json={"segment_ids": ["seg_a"]}).status_code == 200
    assert client.post(f"/api/people/{noise_id}/label-segments", json={"segment_ids": ["seg_b"]}).status_code == 200

    response = client.post("/api/people/auto-attribute", json={"session_id": "ses_lab", "threshold": 0.5})
    assert response.status_code == 200

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select segment_id, person_id from segment_person_overrides where segment_id = 'seg_c'")
    finally:
        conn.close()
    assert rows == [{"segment_id": "seg_c", "person_id": noise_id}]


def test_people_route_exposes_manual_count(tmp_path: Path) -> None:
    # /api/people surfaces manual_count: the enroll-able ground-truth labels per person, distinct
    # from attributed_count (which also includes auto-inferred voiceprint guesses).
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _enroll_two_clusters(config)
    from personal_context_node.speaker_embeddings import label_segments_as_person
    from personal_context_node.speaker_review import upsert_segment_person_override

    # Two manual labels for Alice.
    label_segments_as_person(config=config, person_id="per_alice", segment_ids=["seg_a", "seg_b"])
    # One auto-inferred (voiceprint) attribution for Bob — counts toward attributed_count, NOT manual.
    conn = connect(config.database_path)
    try:
        upsert_segment_person_override(
            conn,
            segment_id="seg_c",
            person_id="per_bob",
            person_label="Bob",
            now="2087-05-10T08:00:00+08:00",
            source="voiceprint",
        )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    people = {p["person_id"]: p for p in client.get("/api/people").json()["people"]}
    assert people["per_alice"]["manual_count"] == 2
    assert people["per_alice"]["attributed_count"] == 2
    assert people["per_bob"]["manual_count"] == 0
    assert people["per_bob"]["attributed_count"] == 1


def test_auto_attribute_route_runs_identify_preserving_manual(tmp_path: Path) -> None:
    # POST /api/people/auto-attribute runs the manual-respecting global identify: a single manual
    # label per person enrolls them, the rest are inferred by voiceprint, and manual labels persist.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_alice", "Alice", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_bob", "Bob", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    # seg_a/seg_b near +x (Alice), seg_c near +y (Bob).
    put_embedding(config=config, segment_id="seg_a", vector=[1.0, 0.0, 0.0])
    put_embedding(config=config, segment_id="seg_b", vector=[0.9, 0.1, 0.0])
    put_embedding(config=config, segment_id="seg_c", vector=[0.0, 1.0, 0.0])
    client = TestClient(create_app(config=config))

    # One manual seed per person.
    assert client.post("/api/people/per_alice/label-segments", json={"segment_ids": ["seg_a"]}).status_code == 200
    assert client.post("/api/people/per_bob/label-segments", json={"segment_ids": ["seg_c"]}).status_code == 200

    response = client.post("/api/people/auto-attribute", json={"session_id": "ses_lab", "threshold": 0.5})
    assert response.status_code == 200
    body = response.json()
    # seg_b is inferred to Alice by voiceprint (manual seeds counted separately in per_person).
    assert body["per_person"] == {"per_alice": 1, "per_bob": 0}
    assert body["threshold"] == 0.5

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select segment_id, person_id, source from segment_person_overrides")
    finally:
        conn.close()
    by_seg = {r["segment_id"]: r for r in rows}
    assert by_seg["seg_a"]["source"] == "manual" and by_seg["seg_a"]["person_id"] == "per_alice"
    assert by_seg["seg_c"]["source"] == "manual" and by_seg["seg_c"]["person_id"] == "per_bob"
    assert by_seg["seg_b"]["source"] == "voiceprint" and by_seg["seg_b"]["person_id"] == "per_alice"


# ---------------------------------------------------------------------------
# Item 2: delete / merge a person
# ---------------------------------------------------------------------------


def _seed_person_with_attribution(config: AppConfig, person_id: str, display_name: str) -> None:
    """Seed a person plus a segment override, a voiceprint, a speaker mapping, and a
    session whose primary_person_id points at them — i.e. a row in every table that
    references persons.person_id, so a delete must cascade across all of them."""
    from personal_context_node.speaker_review import (
        upsert_segment_person_override,
        upsert_speaker_mapping,
    )

    now = "2087-05-10T08:00:00+08:00"
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            (person_id, display_name, "contact", 0, now, now),
        )
        upsert_segment_person_override(conn, segment_id="seg_a", person_id=person_id, person_label=display_name, now=now)
        upsert_speaker_mapping(conn, speaker="spk_a", person_id=person_id, person_label=display_name, now=now)
        conn.execute(
            "insert into person_voiceprints (person_id, dim, vector, n_segments, updated_at) values (?, ?, ?, ?, ?)",
            (person_id, 3, b"\x00\x00\x00", 1, now),
        )
        conn.execute("update sessions set primary_person_id = ? where session_id = 'ses_lab'", (person_id,))
        conn.commit()
    finally:
        conn.close()


def test_delete_person_cascades(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _seed_person_with_attribution(config, "per_dup", "Dup")
    client = TestClient(create_app(config=config))

    # Sanity: the person shows up in the list before deletion.
    assert any(p["person_id"] == "per_dup" for p in client.get("/api/persons").json()["persons"])

    response = client.delete("/api/persons/per_dup")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # The person is gone, and every dependent row is deleted / nulled.
    conn = connect(config.database_path)
    try:
        assert fetch_all(conn, "select person_id from persons where person_id = 'per_dup'") == []
        assert fetch_all(conn, "select segment_id from segment_person_overrides where person_id = 'per_dup'") == []
        assert fetch_all(conn, "select speaker from speaker_mappings where person_id = 'per_dup'") == []
        assert fetch_all(conn, "select person_id from person_voiceprints where person_id = 'per_dup'") == []
        rows = fetch_all(conn, "select primary_person_id from sessions where session_id = 'ses_lab'")
        assert rows == [{"primary_person_id": None}]
    finally:
        conn.close()

    # The person list no longer includes them.
    assert all(p["person_id"] != "per_dup" for p in client.get("/api/persons").json()["persons"])


def test_delete_person_unknown_returns_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    client = TestClient(create_app(config=config))

    assert client.delete("/api/persons/ghost").status_code == 404


def test_clear_segment_attributions_route_returns_segments_to_unidentified(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _seed_person_with_attribution(config, "per_wrong", "Wrong")
    client = TestClient(create_app(config=config))

    response = client.post("/api/people/clear-segment-attributions", json={"segment_ids": ["seg_a"]})

    assert response.status_code == 200
    assert response.json() == {"cleared": 1}
    conn = connect(config.database_path)
    try:
        assert fetch_all(conn, "select segment_id from segment_person_overrides where segment_id = 'seg_a'") == []
        assert fetch_all(conn, "select person_id from persons where person_id = 'per_wrong'") == [{"person_id": "per_wrong"}]
    finally:
        conn.close()


def test_merge_people_reassigns_then_deletes_from(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _seed_person_with_attribution(config, "per_from", "Duplicate")
    # The merge target.
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_into", "Canonical", "contact", 0, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    response = client.post("/api/people/merge", json={"from_id": "per_from", "into_id": "per_into"})
    assert response.status_code == 200
    # One override + one speaker mapping reassigned.
    assert response.json() == {"moved": 2}

    conn = connect(config.database_path)
    try:
        # from_id is gone; its labels were reassigned to into_id (keeping the attribution).
        assert fetch_all(conn, "select person_id from persons where person_id = 'per_from'") == []
        # The override now points at into_id and carries into_id's display name (the canonical label).
        override = fetch_all(conn, "select person_id, person_label from segment_person_overrides where segment_id = 'seg_a'")
        assert override == [{"person_id": "per_into", "person_label": "Canonical"}]
        mapping = fetch_all(conn, "select person_id from speaker_mappings where speaker = 'spk_a'")
        assert mapping == [{"person_id": "per_into"}]
    finally:
        conn.close()


def test_merge_people_missing_returns_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _seed_person_with_attribution(config, "per_from", "Duplicate")
    client = TestClient(create_app(config=config))

    # into_id missing.
    assert client.post("/api/people/merge", json={"from_id": "per_from", "into_id": "ghost"}).status_code == 404
    # from_id missing.
    assert client.post("/api/people/merge", json={"from_id": "ghost", "into_id": "per_from"}).status_code == 404


def test_merge_people_same_id_returns_400(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_labeling_day(config.database_path)
    _seed_person_with_attribution(config, "per_from", "Duplicate")
    client = TestClient(create_app(config=config))

    assert client.post("/api/people/merge", json={"from_id": "per_from", "into_id": "per_from"}).status_code == 400


def _insert_labeling_day(database_path: Path) -> None:
    """Seed one session with three active segments (spk_a x2, spk_b x1) plus an inactive one."""
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_lab", "DJI Mic 3", "/source/lab.wav", 1, 1, "/raw/lab.wav", "sha256:lab", 5000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_lab", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:05+08:00", "derived_from_segments", 3, 1500, "seg_a", "2087-05-10T08:00:03+08:00", "2087-05-10T08:00:03+08:00"),
        )
        # (segment_id, speaker, start_ms, end_ms, absolute_start_at, text, is_active)
        segments = [
            ("seg_a", "spk_a", 0, 500, "2087-05-10T08:00:00+08:00", "alice one", 1),
            ("seg_b", "spk_a", 500, 1000, "2087-05-10T08:00:01+08:00", "alice two", 1),
            ("seg_c", "spk_b", 1000, 1500, "2087-05-10T08:00:02+08:00", "bob one", 1),
            ("seg_inactive", "spk_c", 1500, 2000, "2087-05-10T08:00:03+08:00", "stale", 0),
        ]
        for segment_id, speaker, start_ms, end_ms, abs_start, text, is_active in segments:
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_lab", "chk_lab", "ses_lab", start_ms, end_ms, abs_start, text, "zh", speaker, speaker, f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", is_active, "2087-05-10T08:00:02+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_diarized_day(database_path: Path) -> None:
    """Seed one audio_file on 2087-05-10 with active segments across spk_01/02/03."""
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_paul", "Paul", "self", 1, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 5000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        # The clusters endpoint scopes by the session date_key (the day the UI picker offers), so the
        # segments must belong to a session on that day.
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:05+08:00", "derived_from_segments", 4, 1700, "seg_01a", "2087-05-10T08:00:03+08:00", "2087-05-10T08:00:03+08:00"),
        )
        # (segment_id, speaker_cluster_id, start_ms, end_ms, text, is_active)
        segments = [
            ("seg_01a", "spk_01", 0, 300, "spk01 short", 1),
            ("seg_01b", "spk_01", 300, 1000, "spk01 longer sample text", 1),
            ("seg_02", "spk_02", 1000, 1500, "spk02 sample", 1),
            ("seg_03", "spk_03", 1500, 1700, "spk03 sample", 1),
            # Inactive segment (superseded by an ASR re-run) must be excluded entirely.
            ("seg_inactive", "spk_04", 1700, 4700, "stale", 0),
        ]
        for segment_id, cluster, start_ms, end_ms, text, is_active in segments:
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_test", "chk_1", "ses_test", start_ms, end_ms, text, "zh", cluster, cluster, f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", is_active, "2087-05-10T08:00:02+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_person_and_segment(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_paul", "Paul", "self", 1, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 1000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:01+08:00", "derived_from_segments", 1, 1000, "seg_1", "2087-05-10T08:00:02+08:00", "2087-05-10T08:00:02+08:00"),
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("seg_1", "aud_test", "chk_1", "ses_test", 0, 1000, "你好", "zh", "spk_1", "spk_1", "ev_1", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:02+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
