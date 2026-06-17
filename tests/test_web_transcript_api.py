from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


def test_session_transcript_returns_pending_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/transcripts/sessions/ses_test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ses_test"
    assert payload["review_status"] == "pending_review"
    assert payload["segments"][0]["review_status"] == "pending_review"


def test_review_segment_endpoint_accepts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/transcripts/segments/seg_1/review", json={"status": "accepted", "note": ""})

    assert response.status_code == 200
    assert response.json() == {"segment_id": "seg_1", "status": "accepted"}


def test_review_segment_rejects_invalid_status(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/transcripts/segments/seg_1/review", json={"status": "bogus"})

    assert response.status_code == 400


def test_batch_review_endpoint_accepts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/transcripts/segments/batch-review",
        json={"segment_ids": ["seg_1"], "status": "accepted"},
    )

    assert response.status_code == 200
    assert response.json() == {"updated": 1}

    transcript = client.get("/api/transcripts/sessions/ses_test").json()
    assert transcript["segments"][0]["review_status"] == "accepted"


def test_batch_review_empty_ids_400(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/transcripts/segments/batch-review",
        json={"segment_ids": [], "status": "accepted"},
    )

    assert response.status_code == 400


def test_batch_review_bad_status_400(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/transcripts/segments/batch-review",
        json={"segment_ids": ["seg_1"], "status": "bogus"},
    )

    assert response.status_code == 400


def test_clear_review_endpoint_reverts_to_pending(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    client.post("/api/transcripts/segments/batch-review", json={"segment_ids": ["seg_1"], "status": "accepted"})
    assert client.get("/api/transcripts/sessions/ses_test").json()["segments"][0]["review_status"] == "accepted"

    response = client.post("/api/transcripts/segments/clear-review", json={"segment_ids": ["seg_1"]})

    assert response.status_code == 200
    assert response.json() == {"cleared": 1}

    transcript = client.get("/api/transcripts/sessions/ses_test").json()
    assert transcript["segments"][0]["review_status"] == "pending_review"


def test_clear_review_empty_ids_400(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/transcripts/segments/clear-review", json={"segment_ids": []})

    assert response.status_code == 400


def test_days_and_sessions_for_day_navigation(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    days = client.get("/api/transcripts/days").json()["days"]
    assert [d["day"] for d in days] == ["2087-05-10"]
    assert days[0]["session_count"] == 1

    sessions = client.get("/api/transcripts/days/2087-05-10/sessions").json()["sessions"]
    assert sessions[0]["session_id"] == "ses_test"
    assert sessions[0]["review_status"] == "pending_review"


def test_search_endpoint_returns_results(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/transcripts/search", params={"q": "你好"})

    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 1
    assert results[0]["segment_id"] == "seg_1"
    assert results[0]["session_id"] == "ses_test"
    assert results[0]["day"] == "2087-05-10"


def test_search_endpoint_empty_query_returns_empty(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/transcripts/search", params={"q": "   "})

    assert response.status_code == 200
    assert response.json() == {"results": []}

    # A missing q param is also a 200 with no results (no validation error).
    missing = client.get("/api/transcripts/search")
    assert missing.status_code == 200
    assert missing.json() == {"results": []}


def test_review_queue_endpoint_lists_then_drops_session(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    queue = client.get("/api/transcripts/review-queue").json()["queue"]
    assert len(queue) == 1
    assert queue[0]["session_id"] == "ses_test"
    assert queue[0]["day"] == "2087-05-10"
    assert queue[0]["pending"] == 1
    assert queue[0]["has_flag"] == 0

    # Accept the only segment -> the session leaves the queue.
    client.post("/api/transcripts/segments/batch-review", json={"segment_ids": ["seg_1"], "status": "accepted"})
    assert client.get("/api/transcripts/review-queue").json()["queue"] == []


def test_review_queue_endpoint_honors_limit(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/transcripts/review-queue", params={"limit": 0})
    assert response.status_code == 200
    assert response.json()["queue"] == []


def test_session_transcript_surfaces_resolved_person(tmp_path: Path) -> None:
    # GET /api/transcripts/sessions/{id} exposes the resolved person on each segment so 审核
    # reflects the global voiceprint identity; an unattributed segment has them null.
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    from personal_context_node.speaker_review import upsert_segment_person_override

    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_paul", "Paul", "self", 1, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        upsert_segment_person_override(
            conn, segment_id="seg_1", person_id="per_paul", person_label="Paul", now="2087-05-10T08:00:00+08:00"
        )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    segment = client.get("/api/transcripts/sessions/ses_test").json()["segments"][0]
    assert segment["person_id"] == "per_paul"
    assert segment["person_label"] == "Paul"


def test_session_transcript_person_null_when_unattributed(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    segment = client.get("/api/transcripts/sessions/ses_test").json()["segments"][0]
    assert segment["person_id"] is None
    assert segment["person_label"] is None


def _insert_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
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
            ("seg_1", "aud_test", "chk_1", "ses_test", 0, 1000, "你好", "zh", "self", "self", "ev_1", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:02+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
