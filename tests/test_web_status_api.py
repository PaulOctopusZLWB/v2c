from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
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
