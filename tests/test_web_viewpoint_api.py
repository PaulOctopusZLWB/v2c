from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


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


def _insert_summary(database_path: Path) -> dict[str, object]:
    content = {
        "schema_version": "session_summary.v1",
        "session_id": "ses_test",
        "headline": "一个标题",
        "summary": "一段摘要。",
        "topics": [],
        "decisions": [],
        "todos": [],
        "open_questions": [],
        "core_conclusions": [],
        "per_speaker": [],
    }
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into summaries (
              summary_id, summary_type, target_type, target_id, prompt_version,
              model_name, content_json, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "sum_test", "session", "session", "ses_test",
                "llm_port.session_summary.v1", "mock", json.dumps(content),
                "2087-05-10T09:00:00+08:00", "2087-05-10T09:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return content


def test_patch_segment_text_updates(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.patch("/api/transcripts/segments/seg_1", json={"text": "  改好的  "})

    assert response.status_code == 200
    assert response.json() == {"segment_id": "seg_1", "text": "改好的"}

    segment = client.get("/api/transcripts/sessions/ses_test").json()["segments"][0]
    assert segment["text"] == "改好的"


def test_patch_segment_text_unknown_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.patch("/api/transcripts/segments/seg_missing", json={"text": "x"})

    assert response.status_code == 404


def test_get_viewpoint_returns_payload(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    content = _insert_summary(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/sessions/ses_test/viewpoint")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ses_test"
    assert payload["has_generated"] is True
    assert payload["generated"] == content
    assert payload["effective"] == content
    assert payload["status"] == "draft"
    assert payload["stale"] is False
    assert [s["segment_id"] for s in payload["segments"]] == ["seg_1"]


def test_get_viewpoint_no_summary(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/sessions/ses_test/viewpoint")

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_generated"] is False
    assert payload["effective"] is None
    assert payload["stale"] is False
