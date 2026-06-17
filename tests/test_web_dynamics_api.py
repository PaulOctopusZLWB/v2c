from __future__ import annotations

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
            ("aud_d", "dev", "/s/d.wav", 1, 1, "/r/d.wav", "sha256:d", 9000, "2026-06-01T08:00:00+08:00", "2026-06-01T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_d", "2026-06-01", "2026-06-01T08:00:00+08:00", "2026-06-01T08:00:09+08:00", "derived", 3, 6000, "seg_a1", "2026-06-01T08:00:10+08:00", "2026-06-01T08:00:10+08:00"),
        )
        rows = [
            ("seg_a1", "A", "2026-06-01T08:00:00.000+08:00", 0, 2000),
            ("seg_b1", "B", "2026-06-01T08:00:03.000+08:00", 3000, 5000),
            ("seg_a2", "A", "2026-06-01T08:00:06.000+08:00", 6000, 8000),
        ]
        for seg_id, speaker, abs_start, start_ms, end_ms in rows:
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, absolute_start_at, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (seg_id, "aud_d", "chk", "ses_d", start_ms, end_ms, abs_start, "x", "zh", speaker, speaker, f"ev_{seg_id}", 1.0, "mock", "mock", "mock", 1, "2026-06-01T08:00:10+08:00"),
            )
        conn.commit()
    finally:
        conn.close()


def test_session_dynamics_route(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/sessions/ses_d/dynamics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ses_d"
    assert payload["total_ms"] == 6000  # A:2000 + B:2000 + A:2000
    labels = [s["label"] for s in payload["speakers"]]
    assert labels == ["A", "B"]  # A talks 4000, B 2000
    a = next(s for s in payload["speakers"] if s["label"] == "A")
    assert a["talk_ms"] == 4000
    assert a["turns"] == 2  # [A], [A] separated by B
    # turn-taking: A->B then B->A
    transitions = {(t["from"], t["to"]) for t in payload["transitions"]}
    assert transitions == {("A", "B"), ("B", "A")}
    assert [t["label"] for t in payload["timeline"]] == ["A", "B", "A"]


def test_session_dynamics_route_empty(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    initialize(connect(config.database_path))
    client = TestClient(create_app(config=config))

    response = client.get("/api/sessions/nope/dynamics")
    assert response.status_code == 200
    assert response.json()["speakers"] == []
