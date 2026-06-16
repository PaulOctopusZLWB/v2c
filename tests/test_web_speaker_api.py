from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
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
                (segment_id, "aud_test", "chk_1", None, start_ms, end_ms, text, "zh", cluster, cluster, f"ev_{segment_id}", 1.0, "MockASRAdapter", "mock-asr", "test", is_active, "2087-05-10T08:00:02+08:00"),
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
