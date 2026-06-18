from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.session_viewpoint import DEFAULT_SESSION_PROMPT
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
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


# --- prompt block + generating surfaced on GET viewpoint -----------------


def test_get_viewpoint_surfaces_prompt_and_generating(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    payload = client.get("/api/sessions/ses_test/viewpoint").json()

    assert payload["prompt"] == {
        "effective": DEFAULT_SESSION_PROMPT,
        "default": DEFAULT_SESSION_PROMPT,
        "is_override": False,
    }
    assert payload["generating"] is False


# --- global prompt template GET/PUT --------------------------------------


def test_get_global_prompt_returns_default(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    response = client.get("/api/prompts/session_viewpoint")

    assert response.status_code == 200
    assert response.json() == {
        "template": DEFAULT_SESSION_PROMPT,
        "default": DEFAULT_SESSION_PROMPT,
    }


def test_put_global_prompt_updates_then_resets(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    response = client.put("/api/prompts/session_viewpoint", json={"template": "全局自定义模板。"})
    assert response.status_code == 200
    assert response.json()["template"] == "全局自定义模板。"
    assert client.get("/api/prompts/session_viewpoint").json()["template"] == "全局自定义模板。"

    # empty template resets to default.
    reset = client.put("/api/prompts/session_viewpoint", json={"template": ""})
    assert reset.status_code == 200
    assert reset.json()["template"] == DEFAULT_SESSION_PROMPT
    assert client.get("/api/prompts/session_viewpoint").json()["template"] == DEFAULT_SESSION_PROMPT


# --- per-session prompt override PUT -------------------------------------


def test_put_session_prompt_override_sets_and_clears(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.put(
        "/api/sessions/ses_test/viewpoint/prompt", json={"template": "本会话专属模板。"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["effective"] == "本会话专属模板。"
    assert body["is_override"] is True

    # GET viewpoint now reflects the per-session override.
    vp = client.get("/api/sessions/ses_test/viewpoint").json()
    assert vp["prompt"]["effective"] == "本会话专属模板。"
    assert vp["prompt"]["is_override"] is True

    # null clears the override -> back to the global/default.
    cleared = client.put("/api/sessions/ses_test/viewpoint/prompt", json={"template": None})
    assert cleared.status_code == 200
    assert cleared.json()["is_override"] is False
    assert cleared.json()["effective"] == DEFAULT_SESSION_PROMPT


# --- manual generate ------------------------------------------------------


def test_post_generate_enqueues_summarize_session_task(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    app = create_app(config=config)
    # Stub the worker so the test doesn't spawn a real drain thread.
    app.state.worker.start = lambda *a, **k: True  # type: ignore[method-assign]
    client = TestClient(app)

    response = client.post("/api/sessions/ses_test/viewpoint/generate")

    assert response.status_code == 200
    assert response.json() == {"enqueued": True, "session_id": "ses_test"}

    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            "select task_type, target_type, target_id, status, priority from tasks "
            "where task_type = 'summarize_session' and target_id = 'ses_test'",
        )
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["target_type"] == "session"
    assert rows[0]["status"] == "pending"

    # the GET viewpoint now reports generating=True (an active summarize_session task exists).
    vp = client.get("/api/sessions/ses_test/viewpoint").json()
    assert vp["generating"] is True


def test_post_generate_rearms_a_succeeded_task(tmp_path: Path) -> None:
    """重新生成 must re-run even when the session was already summarized: enqueue_task dedups on
    (type, target) regardless of status, so the route uses rerun_task to re-arm the SUCCEEDED task
    back to 'pending'. Otherwise regenerate would silently no-op for every existing session."""
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    conn = connect(config.database_path)
    try:
        conn.execute(
            "insert into tasks (task_id, task_type, target_type, target_id, status, priority, "
            "max_retries, available_at, created_at, updated_at) values "
            "('task_old', 'summarize_session', 'session', 'ses_test', 'succeeded', 100, 3, "
            "'2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    app = create_app(config=config)
    app.state.worker.start = lambda *a, **k: True  # type: ignore[method-assign]
    client = TestClient(app)

    assert client.post("/api/sessions/ses_test/viewpoint/generate").status_code == 200

    conn = connect(config.database_path)
    try:
        rows = fetch_all(
            conn,
            "select task_id, status from tasks where task_type = 'summarize_session' and target_id = 'ses_test'",
        )
    finally:
        conn.close()
    # same task re-armed (not a duplicate), pending again
    assert len(rows) == 1
    assert rows[0]["task_id"] == "task_old"
    assert rows[0]["status"] == "pending"
    assert client.get("/api/sessions/ses_test/viewpoint").json()["generating"] is True


def test_post_generate_unknown_session_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    app = create_app(config=config)
    app.state.worker.start = lambda *a, **k: True  # type: ignore[method-assign]
    client = TestClient(app)

    response = client.post("/api/sessions/ses_missing/viewpoint/generate")

    assert response.status_code == 404


# --- edit the result: PUT / DELETE viewpoint ------------------------------


def _edited_content() -> dict[str, object]:
    return {
        "schema_version": "session_summary.v1",
        "session_id": "ses_test",
        "headline": "手动改过的标题",
        "summary": "手动编辑后的摘要。",
        "topics": [],
        "decisions": [],
        "todos": [],
        "open_questions": [],
        "core_conclusions": [],
        "per_speaker": [],
    }


def test_put_viewpoint_valid_stores_edit_and_returns_state(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.put(
        "/api/sessions/ses_test/viewpoint", json={"content": _edited_content()}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["edited"] == _edited_content()
    assert body["effective"] == _edited_content()
    assert body["status"] == "edited"


def test_put_viewpoint_invalid_doc_returns_400(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path)
    client = TestClient(create_app(config=config))

    # missing required fields + a stray field -> schema validation fails.
    response = client.put(
        "/api/sessions/ses_test/viewpoint", json={"content": {"headline": "x", "bogus": 1}}
    )

    assert response.status_code == 400
    # nothing stored: still draft on a fresh GET.
    assert client.get("/api/sessions/ses_test/viewpoint").json()["status"] == "draft"


def test_delete_viewpoint_edit_reverts_to_draft(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    content = _insert_summary(config.database_path)
    client = TestClient(create_app(config=config))
    client.put("/api/sessions/ses_test/viewpoint", json={"content": _edited_content()})

    response = client.delete("/api/sessions/ses_test/viewpoint/edit")

    assert response.status_code == 200
    body = response.json()
    assert body["edited"] is None
    assert body["effective"] == content
    assert body["status"] == "draft"


# --- manual one-way publish: POST viewpoint/publish -----------------------


def test_post_publish_writes_note_and_flips_status(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path)
    client = TestClient(create_app(config=config))
    # publish the EDITED content so we can assert the edit lands in the note.
    client.put("/api/sessions/ses_test/viewpoint", json={"content": _edited_content()})

    response = client.post("/api/sessions/ses_test/viewpoint/publish")

    assert response.status_code == 200
    body = response.json()
    assert body["published_at"] is not None
    note_path = Path(body["note_path"])
    assert note_path.exists()
    assert "手动改过的标题" in note_path.read_text(encoding="utf-8")

    vp = client.get("/api/sessions/ses_test/viewpoint").json()
    assert vp["status"] == "published"
    assert vp["note_path"] == str(note_path)


def test_post_publish_errors_when_nothing_generated(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/sessions/ses_test/viewpoint/publish")

    assert response.status_code in (400, 409)
