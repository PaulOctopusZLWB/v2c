from __future__ import annotations

import math
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.web.app import create_app


def test_import_wait_then_review_then_status_smoke(tmp_path: Path) -> None:
    source = tmp_path / "NO NAME"
    _write_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", vad_backend="mock", asr_backend="mock", llm_backend="mock")
    client = TestClient(create_app(config=config))

    imported = client.post("/api/pipeline/import", json={"source_dir": str(source), "wait": True})
    assert imported.status_code == 200
    assert imported.json()["imported_files"] == 1

    tasks = client.get("/api/status/tasks").json()["tasks"]
    assert any(row["status"] == "succeeded" for row in tasks)


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        frames = bytearray()
        for index in range(16000):
            sample = int(10000 * math.sin(2 * math.pi * 440 * index / 16000))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))
