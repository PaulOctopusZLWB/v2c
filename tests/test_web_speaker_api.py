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
