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


def test_finalize_writes_the_export_artifact_pair(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_api_identity_session(config.database_path)
    client = TestClient(create_app(config=config))

    # 400 before anyone is confirmed present — attendance IS the finalization criterion.
    blocked = client.post("/api/sessions/ses_1/finalize")
    assert blocked.status_code == 400

    present = client.post("/api/sessions/ses_1/participants", json={"person_id": "per_a", "status": "present"})
    assert present.status_code == 200, present.text
    # Confirming attendance runs nothing LLM-shaped anymore (codex owns synthesis).
    conn = connect(config.database_path)
    try:
        assert conn.execute("select 1 from tasks where task_type = 'summarize_session'").fetchone() is None
    finally:
        conn.close()

    finalized = client.post("/api/sessions/ses_1/finalize")
    assert finalized.status_code == 200, finalized.text
    body = finalized.json()
    md = Path(body["export_md_path"])
    js = Path(body["export_json_path"])
    assert md.exists() and js.exists()
    # Exports live with the project data dir, per date, per session.
    assert str(md).startswith(str(tmp_path / "data")) and "exports/sessions/2087-05-10" in str(md)

    text = md.read_text(encoding="utf-8")
    assert "Alice(出现)" in text
    assert "hello" in text  # FULL transcript in the md
    assert "spk_01" not in text  # machine vocabulary never reaches the artifact

    import json as _json

    payload = _json.loads(js.read_text(encoding="utf-8"))
    assert payload["attendance"]["present"][0]["display_name"] == "Alice"
    assert payload["segments"][0]["audio_url"] == "/api/audio/segments/seg_1"
    assert payload["segments"][0]["speaker_display"] == "Alice"

    # The review payload reflects the finalized state; re-finalizing is idempotent.
    review = client.get("/api/sessions/ses_1/identity-review").json()
    assert review["can_finalize"] is True
    assert review["finalized"]["export_md_path"] == str(md)
    assert client.post("/api/sessions/ses_1/finalize").status_code == 200


def test_finalize_labels_unidentified_voices_without_machine_ids(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _seed_api_identity_session(config.database_path)
    conn = connect(config.database_path)
    try:
        # An unattributed second voice: must surface as 声音A, never as its spk_02/vp label.
        conn.execute("insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, is_active) values ('seg_2', 'aud_1', 'chk_2', 'ses_1', 1000, 2000, '第二个声音', 'zh', 'spk_02', 'vp_007', 'ev_2', 1)")
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))
    client.post("/api/sessions/ses_1/participants", json={"person_id": "per_a", "status": "present"})

    body = client.post("/api/sessions/ses_1/finalize").json()

    assert body["unidentified_voices"] == [{"label": "声音A", "segment_count": 1}]
    text = Path(body["export_md_path"]).read_text(encoding="utf-8")
    assert "声音A" in text and "第二个声音" in text
    assert "vp_007" not in text and "spk_02" not in text


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
