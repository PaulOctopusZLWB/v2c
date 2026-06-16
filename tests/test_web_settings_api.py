from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.web.app import create_app


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")


def test_get_settings_returns_effective_form(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GLM_MODEL", raising=False)
    config = _config(tmp_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["asr_mode"] == config.asr_mode
    assert body["glm_model"] == "glm-4-flash"
    assert body["glm_thinking"] is False
    assert "asr_preset_spk_num" in body
    assert "glm_base_url" in body


def test_put_settings_updates_and_next_get_reflects(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = TestClient(create_app(config=config))

    response = client.put(
        "/api/settings",
        json={"asr_mode": "diarize", "asr_preset_spk_num": 3, "glm_model": "glm-5.1"},
    )
    assert response.status_code == 200
    assert response.json()["asr_mode"] == "diarize"
    assert response.json()["asr_preset_spk_num"] == 3
    assert response.json()["glm_model"] == "glm-5.1"

    again = client.get("/api/settings")
    assert again.status_code == 200
    assert again.json()["asr_mode"] == "diarize"
    assert again.json()["asr_preset_spk_num"] == 3
    assert again.json()["glm_model"] == "glm-5.1"


def test_put_settings_invalid_asr_mode_returns_400(tmp_path: Path) -> None:
    config = _config(tmp_path)
    client = TestClient(create_app(config=config))

    response = client.put("/api/settings", json={"asr_mode": "nonsense"})

    assert response.status_code == 400
