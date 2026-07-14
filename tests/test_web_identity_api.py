from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


def test_identity_review_api_records_participants_and_not_person_feedback(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_api_identity_session(config.database_path)
    client = TestClient(create_app(config=config))

    participant = client.post("/api/sessions/ses_1/participants", json={"person_id": "per_a", "status": "present"})
    not_person = client.post("/api/identity/not-person", json={"session_id": "ses_1", "segment_ids": ["seg_1"], "person_id": "per_a"})
    review = client.get("/api/sessions/ses_1/identity-review")

    assert participant.status_code == 200, participant.text
    assert not_person.status_code == 200, not_person.text
    assert review.status_code == 200, review.text
    body = review.json()
    assert body["participants"] == [{"person_id": "per_a", "display_name": "Alice", "status": "present"}]
    assert body["negative_feedback_count"] == 1
    assert body["can_summarize"] is True


def test_not_person_feedback_clears_the_contradicted_attribution(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_api_identity_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post(
        "/api/identity/not-person",
        json={"session_id": "ses_1", "segment_ids": ["seg_1"], "person_id": "per_a"},
    )

    assert response.status_code == 200, response.text
    conn = connect(config.database_path)
    try:
        override = conn.execute("select 1 from segment_person_overrides where segment_id = 'seg_1'").fetchone()
        feedback = conn.execute("select 1 from segment_identity_negative_feedback where segment_id = 'seg_1' and person_id = 'per_a'").fetchone()
    finally:
        conn.close()
    assert override is None  # "不是 Alice" removed the contradicted attribution
    assert feedback is not None  # ...and the negative pair keeps auto-attribute from re-adding it


def test_absent_participant_cascades_and_identify_endpoint_reruns(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_api_identity_session(config.database_path)
    conn = connect(config.database_path)
    try:
        # A second person with an INFERRED attribution in the session — the absent-cascade target.
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_b', 'Bob', 'contact', 0, 'now', 'now')")
        conn.execute("insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, is_active) values ('seg_2', 'aud_1', 'chk_2', 'ses_1', 1000, 2000, 'hi', 'zh', 'spk_02', 'spk_02', 'ev_2', 1)")
        conn.execute("insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values ('seg_2', 'Bob', 'now', 'per_b', 'voiceprint')")
        conn.execute("insert into segment_embeddings (segment_id, model, dim, vector, created_at) values ('seg_2', 'cam++', 3, x'0000803F0000000000000000', 'now')")
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    absent = client.post("/api/sessions/ses_1/participants", json={"person_id": "per_b", "status": "absent"})
    assert absent.status_code == 200, absent.text
    body = absent.json()
    assert body["cascade"]["cascade"] == "absent"
    assert body["cascade"]["cleared"] == 1
    conn = connect(config.database_path)
    try:
        assert conn.execute("select 1 from segment_person_overrides where segment_id = 'seg_2'").fetchone() is None
    finally:
        conn.close()

    rerun = client.post("/api/sessions/ses_1/identify")
    assert rerun.status_code == 200, rerun.text
    stats = rerun.json()
    assert stats["session_id"] == "ses_1"
    assert stats["excluded_absent"] == ["per_b"]  # review constraints honoured on re-trigger


def test_first_present_verdict_releases_the_session_summary(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_api_identity_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/sessions/ses_1/participants", json={"person_id": "per_a", "status": "present"})

    assert response.status_code == 200, response.text
    assert response.json()["summary_enqueued"] is True
    conn = connect(config.database_path)
    try:
        task = conn.execute("select status from tasks where task_type = 'summarize_session' and target_id = 'ses_1'").fetchone()
    finally:
        conn.close()
    assert task is not None  # the background drain owns it from here

    # A later present verdict once a summary row exists must NOT enqueue again (no repeat LLM
    # burn). Simulate the summary having been generated.
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into summaries (summary_id, summary_type, target_type, target_id, content_json, created_at, updated_at) values ('sum_1', 'session', 'session', 'ses_1', '{}', 'now', 'now')"
        )
        conn.commit()
    finally:
        conn.close()
    again = client.post("/api/sessions/ses_1/participants", json={"person_id": "per_a", "status": "present"})
    assert again.status_code == 200
    assert again.json()["summary_enqueued"] is False


def _seed_api_identity_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute("insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values ('per_a', 'Alice', 'contact', 0, 'now', 'now')")
        conn.execute("insert into audio_files (audio_file_id, source_device, source_path, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values ('aud_1', 'dev', '/tmp/a.wav', '/tmp/a.wav', 'sha', 1000, '2087-05-10T08:00:00+08:00', 'now', 'imported')")
        conn.execute("insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values ('ses_1', '2087-05-10', '2087-05-10T08:00:00+08:00', '2087-05-10T08:01:00+08:00', 'derived', 1, 1000, 'seg_1', 'now', 'now')")
        conn.execute("insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, is_active) values ('seg_1', 'aud_1', 'chk_1', 'ses_1', 0, 1000, 'hello', 'zh', 'spk_01', 'spk_01', 'ev_1', 1)")
        conn.execute("insert into segment_person_overrides (segment_id, person_label, updated_at, person_id, source) values ('seg_1', 'Alice', 'now', 'per_a', 'manual')")
        conn.commit()
    finally:
        conn.close()
