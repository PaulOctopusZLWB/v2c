from __future__ import annotations

import math
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.web.app import create_app


def test_import_enqueues_vad_task_and_does_not_create_parallel_run_table(tmp_path: Path) -> None:
    source = tmp_path / "NO NAME"
    _write_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_backend="mock", vad_backend="mock", llm_backend="mock")
    client = TestClient(create_app(config=config))

    # wait=True imports synchronously so the enqueued vad task is observable right away.
    response = client.post("/api/pipeline/import", json={"source_dir": str(source), "wait": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported_files"] == 1
    assert payload["queued"] is True  # synchronous import enqueues then drains
    conn = connect(config.database_path)
    try:
        initialize(conn)
        tables = {row["name"] for row in fetch_all(conn, "select name from sqlite_master where type='table'")}
        vad_tasks = fetch_all(conn, "select task_id from tasks where task_type = 'vad'")
    finally:
        conn.close()
    assert "pipeline_runs" not in tables  # no parallel orchestrator
    assert len(vad_tasks) == 1


def test_import_async_returns_started_without_blocking(tmp_path: Path) -> None:
    source = tmp_path / "NO NAME"
    _write_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_backend="mock", vad_backend="mock", llm_backend="mock")
    client = TestClient(create_app(config=config))

    # wait=False (default): the copy runs in a background thread; the request returns
    # immediately with the started/importing shape. We assert only the response shape
    # (not eventual completion) so the test never sleeps on the worker.
    response = client.post("/api/pipeline/import", json={"source_dir": str(source), "wait": False})

    assert response.status_code == 200
    payload = response.json()
    assert payload["started"] is True
    assert payload["importing"] is True


def test_import_with_wait_runs_pipeline_through_mock_backends(tmp_path: Path) -> None:
    source = tmp_path / "NO NAME"
    _write_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_backend="mock", vad_backend="mock", llm_backend="mock")
    client = TestClient(create_app(config=config))

    response = client.post("/api/pipeline/import", json={"source_dir": str(source), "wait": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported_files"] == 1
    assert payload["drain"]["status"] in {"complete", "step_limit"}
    assert payload["drain"]["tasks_succeeded"] >= 1


def test_stop_is_idempotent_when_worker_idle(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    response = client.post("/api/pipeline/stop")

    assert response.status_code == 200
    assert response.json()["stop_requested"] is True


def test_events_endpoint_is_served_at_api_events(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    with client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        first = next(response.iter_lines())
        assert first == "event: status.snapshot"

    # The wrong path must NOT exist.
    assert client.get("/api/pipeline/events").status_code == 404


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
