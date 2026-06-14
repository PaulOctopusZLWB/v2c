from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig, DeviceDiscoveryConfig
from personal_context_node.web.app import create_app


def test_devices_endpoint_lists_detected_volume(tmp_path: Path) -> None:
    volumes = tmp_path / "Volumes"
    root = volumes / "NO NAME"
    root.mkdir(parents=True)
    (root / "TX01_MIC001_20870510_090000_orig.wav").write_bytes(b"RIFF0000WAVE")
    config = AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        dji_mic_3=DeviceDiscoveryConfig(volume_root=volumes, volume_name_patterns=("NO NAME",)),
    )
    client = TestClient(create_app(config=config))

    response = client.get("/api/devices")

    assert response.status_code == 200
    sources = response.json()["sources"]
    assert any(s["kind"] == "device" and s["audio_count"] == 1 for s in sources)
