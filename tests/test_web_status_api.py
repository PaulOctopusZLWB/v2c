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
