from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.home_overview import home_overview
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.transcript_review import review_segment
from personal_context_node.web.app import create_app


def _insert_fixture(database_path: Path) -> None:
    """Two days, two sessions, mixed review/embedding/emotion coverage + an enrolled person.

    Day 2087-05-11 / ses_b is the most recent (latest started_at); day 2087-05-10 / ses_a
    is older. ses_a has 2 segments (seg_a1 accepted, seg_a2 pending). ses_b has 1 segment
    (seg_b1 pending). One person is enrolled (a person_voiceprints row), one is not. One
    segment carries an embedding, one carries an emotion.
    """
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_1", "DJI Mic 3", "/src/a.wav", 1, 1, "/raw/a.wav", "sha256:a", 3000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        # Two sessions across two days; ses_b is the most recent by started_at.
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_a", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:02+08:00", "derived", 2, 2000, "seg_a1", "x", "x"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_b", "2087-05-11", "2087-05-11T09:00:00+08:00", "2087-05-11T09:00:01+08:00", "derived", 1, 1000, "seg_b1", "x", "x"),
        )
        segs = [
            ("seg_a1", "ses_a", "self", "ev_a1"),
            ("seg_a2", "ses_a", "spk_1", "ev_a2"),
            ("seg_b1", "ses_b", "spk_1", "ev_b1"),
        ]
        for index, (segment_id, session_id, speaker, evidence_id) in enumerate(segs):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_1", f"chk_{segment_id}", session_id, 0, 1000, f"text {index}", "zh", speaker, speaker, evidence_id, 1.0, "mock", "mock", "mock", 1, "x"),
            )
        # Persons: one enrolled (has a voiceprint), one not.
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_self", "我", "self", 1, "x", "x"),
        )
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_b", "李雷", "contact", 0, "x", "x"),
        )
        conn.execute(
            "insert into person_voiceprints (person_id, dim, vector, n_segments, updated_at) values (?, ?, ?, ?, ?)",
            ("per_self", 4, b"\x00\x00\x00\x00", 3, "x"),
        )
        # One embedding, one emotion (distinct segments).
        conn.execute(
            "insert into segment_embeddings (segment_id, model, dim, vector, created_at) values (?, ?, ?, ?, ?)",
            ("seg_a1", "campplus", 4, b"\x00\x00\x00\x00", "x"),
        )
        conn.execute(
            "insert into segment_emotions (segment_id, model, label, scores_json, created_at) values (?, ?, ?, ?, ?)",
            ("seg_b1", "emotion2vec", "happy", "{}", "x"),
        )
        conn.commit()
    finally:
        conn.close()


def test_home_overview_counts_review_people_coverage(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_fixture(config.database_path)
    # seg_a1 accepted; seg_a2 + seg_b1 stay pending -> 2 pending segments across 2 sessions.
    review_segment(config=config, segment_id="seg_a1", status="accepted", note="")

    overview = home_overview(config=config)

    assert overview["review"] == {"pending_sessions": 2, "pending_segments": 2}
    assert overview["people"] == {"total": 2, "enrolled": 1}
    assert overview["coverage"] == {
        "days": 2,
        "sessions": 2,
        "segments": 3,
        "embedded": 1,
        "emoted": 1,
    }


def test_home_overview_recent_sessions_order_and_latest_day(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_fixture(config.database_path)
    review_segment(config=config, segment_id="seg_a1", status="accepted", note="")

    overview = home_overview(config=config)

    recent = overview["recent_sessions"]
    assert [r["session_id"] for r in recent] == ["ses_b", "ses_a"]  # newest first
    assert recent[0]["day"] == "2087-05-11"
    assert recent[0]["segment_count"] == 1
    assert recent[0]["review_status"] == "pending_review"
    assert recent[1]["session_id"] == "ses_a"
    assert recent[1]["review_status"] == "pending_review"  # seg_a2 still pending
    assert overview["latest_day"] == "2087-05-11"


def test_home_overview_empty_db(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
    finally:
        conn.close()

    overview = home_overview(config=config)

    assert overview["review"] == {"pending_sessions": 0, "pending_segments": 0}
    assert overview["people"] == {"total": 0, "enrolled": 0}
    assert overview["coverage"] == {"days": 0, "sessions": 0, "segments": 0, "embedded": 0, "emoted": 0}
    assert overview["recent_sessions"] == []
    assert overview["latest_day"] is None


def test_home_overview_route(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_fixture(config.database_path)
    review_segment(config=config, segment_id="seg_a1", status="accepted", note="")
    client = TestClient(create_app(config=config))

    response = client.get("/api/home/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review"]["pending_sessions"] == 2
    assert payload["people"]["enrolled"] == 1
    assert payload["coverage"]["embedded"] == 1
    assert payload["coverage"]["emoted"] == 1
    assert len(payload["recent_sessions"]) == 2
    assert payload["latest_day"] == "2087-05-11"
