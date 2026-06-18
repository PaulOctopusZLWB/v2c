from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.session_viewpoint import (
    session_fingerprint,
    set_segment_text,
    viewpoint_state,
)
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def _insert_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 2000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:02+08:00", "derived_from_segments", 2, 2000, "seg_1", "2087-05-10T08:00:02+08:00", "2087-05-10T08:00:02+08:00"),
        )
        for index, (segment_id, text) in enumerate([("seg_1", "你好"), ("seg_2", "再见")]):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, absolute_end_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    segment_id, "aud_test", f"chk_{segment_id}", "ses_test",
                    index * 1000, (index + 1) * 1000,
                    f"2087-05-10T08:00:0{index}+08:00", f"2087-05-10T08:00:0{index + 1}+08:00",
                    text, "zh", "self", "self", f"ev_{index + 1}", 1.0,
                    "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:02+08:00",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _generated_content() -> dict[str, object]:
    return {
        "schema_version": "session_summary.v1",
        "session_id": "ses_test",
        "headline": "一个标题",
        "summary": "一段摘要。",
        "topics": [],
        "decisions": [],
        "todos": [],
        "open_questions": [],
        "core_conclusions": ["核心结论"],
        "per_speaker": [],
    }


def _insert_summary(database_path: Path, content: dict[str, object]) -> None:
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


def _insert_sidecar(database_path: Path, **columns: object) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        columns = {"session_id": "ses_test", "updated_at": "2087-05-10T10:00:00+08:00", **columns}
        names = ", ".join(columns)
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"insert into session_viewpoint_state ({names}) values ({placeholders})",
            tuple(columns.values()),
        )
        conn.commit()
    finally:
        conn.close()


# --- session_fingerprint -------------------------------------------------


def test_session_fingerprint_is_stable() -> None:
    segments = [
        {"segment_id": "s1", "text": "a", "speaker": "self", "person_label": None},
        {"segment_id": "s2", "text": "b", "speaker": "spk_1", "person_label": "Alice"},
    ]
    assert session_fingerprint(segments) == session_fingerprint(segments)


def test_session_fingerprint_is_order_sensitive() -> None:
    a = {"segment_id": "s1", "text": "a", "speaker": "self", "person_label": None}
    b = {"segment_id": "s2", "text": "b", "speaker": "self", "person_label": None}
    assert session_fingerprint([a, b]) != session_fingerprint([b, a])


def test_session_fingerprint_changes_when_text_changes() -> None:
    before = [{"segment_id": "s1", "text": "a", "speaker": "self", "person_label": None}]
    after = [{"segment_id": "s1", "text": "A", "speaker": "self", "person_label": None}]
    assert session_fingerprint(before) != session_fingerprint(after)


def test_session_fingerprint_changes_when_speaker_changes() -> None:
    before = [{"segment_id": "s1", "text": "a", "speaker": "self", "person_label": None}]
    # A speaker correction surfaces via person_label, which should mark the result stale.
    after = [{"segment_id": "s1", "text": "a", "speaker": "self", "person_label": "Alice"}]
    assert session_fingerprint(before) != session_fingerprint(after)


# --- viewpoint_state -----------------------------------------------------


def test_viewpoint_state_no_summary(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["session_id"] == "ses_test"
    assert state["has_generated"] is False
    assert state["generated"] is None
    assert state["effective"] is None
    assert state["edited"] is None
    assert state["stale"] is False
    assert state["status"] == "draft"
    assert state["published_at"] is None
    assert state["note_path"] is None
    assert [s["segment_id"] for s in state["segments"]] == ["seg_1", "seg_2"]
    assert state["segments"][0]["text"] == "你好"
    assert state["segments"][0]["speaker"] == "self"
    assert "person_label" in state["segments"][0]


def test_viewpoint_state_with_generated(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["has_generated"] is True
    assert state["generated"] == _generated_content()
    assert state["effective"] == _generated_content()
    assert state["edited"] is None


def test_viewpoint_state_edited_overrides_generated(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())
    edited = {**_generated_content(), "headline": "手动改过的标题"}
    _insert_sidecar(config.database_path, edited_content_json=json.dumps(edited), status="edited")

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["edited"] == edited
    assert state["effective"] == edited
    assert state["generated"] == _generated_content()
    assert state["status"] == "edited"


def test_viewpoint_state_stale_when_fingerprint_mismatches(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())
    _insert_sidecar(config.database_path, source_fingerprint="stale-does-not-match")

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["stale"] is True


def test_viewpoint_state_not_stale_when_fingerprint_matches(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    _insert_summary(config.database_path, _generated_content())
    # Compute the live fingerprint from the seeded segments and stash it as the stored one.
    live = viewpoint_state(config=config, session_id="ses_test")
    fingerprint = session_fingerprint(live["segments"])
    _insert_sidecar(config.database_path, source_fingerprint=fingerprint)

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["stale"] is False


def test_viewpoint_state_not_stale_without_generated(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    # A stored fingerprint but no generated summary -> never stale.
    _insert_sidecar(config.database_path, source_fingerprint="anything")

    state = viewpoint_state(config=config, session_id="ses_test")

    assert state["has_generated"] is False
    assert state["stale"] is False


# --- set_segment_text ----------------------------------------------------


def test_set_segment_text_updates_and_returns_true(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    assert set_segment_text(config=config, segment_id="seg_1", text="  改好的文字  ") is True

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select text from transcript_segments where segment_id = ?", ("seg_1",))
    finally:
        conn.close()
    assert rows[0]["text"] == "改好的文字"


def test_set_segment_text_unknown_id_returns_false(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)

    assert set_segment_text(config=config, segment_id="seg_missing", text="x") is False
