from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.tasks import enqueue_task
from personal_context_node.web.app import create_app


def test_web_health_returns_local_runtime_metadata(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "host": "127.0.0.1",
        "data_dir": str(config.data_dir),
        "obsidian_vault": str(config.obsidian_vault),
        "require_accepted_transcripts": False,
    }


def test_status_tasks_lists_enqueued_task(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
    finally:
        conn.close()
    enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="aud_x")
    client = TestClient(create_app(config=config))

    response = client.get("/api/status/tasks")

    assert response.status_code == 200
    rows = response.json()["tasks"]
    assert any(row["task_type"] == "vad" and row["status"] == "pending" for row in rows)


def test_status_overview_reports_counts_and_worker_idle(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    response = client.get("/api/status/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["worker_running"] is False
    assert "status_counts" in payload


def test_day_status_returns_per_day_processing_or_ready(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = "2026-06-01T10:00:00+00:00"
        # day 2026-06-01: audio file + a pending asr task -> processing
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns,
              local_raw_path, sha256, duration_ms, recorded_at, imported_at, status
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("aud_day1", "test", "/s1.wav", 0, 0, "/l1.wav", "sha:1", 1000, "2026-06-01T10:00:00+00:00", now, "imported"),
        )
        conn.execute(
            """
            insert into tasks (task_id, task_type, target_type, target_id, status, available_at, created_at, updated_at)
            values ('task_asr1', 'asr', 'audio_file', 'aud_day1', 'pending', ?, ?, ?)
            """,
            (now, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    response = client.get("/api/transcripts/day-status")

    assert response.status_code == 200
    rows = response.json()["days"]
    assert isinstance(rows, list)
    day1 = next((r for r in rows if r["day"] == "2026-06-01"), None)
    assert day1 is not None
    assert day1["status"] == "processing"


def test_root_returns_api_marker_when_frontend_not_built(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["app"] == "Personal Context Node"
