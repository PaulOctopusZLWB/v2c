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
    _write_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
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
    _write_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
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
    _write_wav(source / "TX02_MIC001_20250610_173550_orig.wav")
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
        lines = list(response.iter_lines())
        # The stream must emit a status.summary event before it closes.
        assert "event: status.summary" in lines, f"expected status.summary in stream lines: {lines}"

    # The wrong path must NOT exist.
    assert client.get("/api/pipeline/events").status_code == 404


def test_events_status_summary_has_compact_shape(tmp_path: Path) -> None:
    # The status.summary payload must contain status_counts, total, active_stage,
    # current_target and import_progress — NOT a full tasks array.
    import json

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    with client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        lines = list(response.iter_lines())

    # Find the data line immediately after the first status.summary event line.
    summary_data: dict | None = None
    for i, line in enumerate(lines):
        if line == "event: status.summary" and i + 1 < len(lines):
            data_line = lines[i + 1]
            assert data_line.startswith("data: ")
            summary_data = json.loads(data_line[len("data: "):])
            break

    assert summary_data is not None, "no status.summary data found"
    assert "status_counts" in summary_data
    assert "total" in summary_data
    assert "import_progress" in summary_data
    # Per-stage breakdown + ETA travel in the compact summary so the header doesn't need
    # the full task list.
    assert "stage_counts" in summary_data
    assert "eta_seconds" in summary_data
    # Settled/failed totals (with correct retryable-exhausted semantics) ride along too, so the
    # header's done/failed counters don't have to fetch the full task list.
    assert "done_total" in summary_data
    assert "failed_total" in summary_data
    assert "worker_running" in summary_data
    # Must NOT be a full tasks dump — no 'tasks' key in the compact payload.
    assert "tasks" not in summary_data


def test_events_summary_counts_exhausted_retryable_as_done_and_failed(tmp_path: Path) -> None:
    # done_total/failed_total must treat a failed_retryable task that has exhausted its retries
    # (retry_count >= max_retries — the claimer will never pick it up again) as settled-and-failed,
    # while a retryable task with attempts left is NOT counted as done. (No 'pending' task here:
    # pending is an active status that would hold the buffering-TestClient stream open forever;
    # failed_retryable is inactive, so the stream still closes after its snapshot.)
    import json

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = "2026-06-01T10:00:00+00:00"
        # (task_id, status, retry_count, max_retries)
        for task_id, status, retry_count, max_retries in [
            ("ok", "succeeded", 1, 3),
            ("terminal", "failed_terminal", 1, 3),
            ("exhausted", "failed_retryable", 3, 3),  # no retries left -> settled + failed
            ("retrying", "failed_retryable", 1, 3),  # retries left -> NOT done, NOT failed
        ]:
            conn.execute(
                """
                insert into tasks (task_id, task_type, target_type, target_id, status,
                                   retry_count, max_retries, available_at, created_at, updated_at)
                values (?, 'asr', 'audio_chunk', ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, task_id, status, retry_count, max_retries, now, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    with client.stream("GET", "/api/events") as response:
        lines = list(response.iter_lines())
    summary_data: dict | None = None
    for i, line in enumerate(lines):
        if line == "event: status.summary" and i + 1 < len(lines):
            summary_data = json.loads(lines[i + 1][len("data: "):])
            break

    assert summary_data is not None
    # succeeded + failed_terminal + exhausted-retryable = 3 settled; the still-retrying task excluded.
    assert summary_data["done_total"] == 3
    # failed = terminal + exhausted-retryable (succeeded excluded).
    assert summary_data["failed_total"] == 2


def test_retry_failed_resets_all_failed_tasks(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        now = "2026-06-01T10:00:00+00:00"
        for task_id, status in [
            ("task_ft1", "failed_terminal"),
            ("task_fr1", "failed_retryable"),
            ("task_ok1", "succeeded"),
        ]:
            conn.execute(
                """
                insert into tasks (task_id, task_type, target_type, target_id, status, available_at, created_at, updated_at)
                values (?, 'asr', 'audio_chunk', ?, ?, ?, ?, ?)
                """,
                (task_id, task_id, status, now, now, now),
            )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    resp = client.post("/api/pipeline/retry-failed")
    assert resp.status_code == 200
    assert resp.json()["retried"] >= 1

    # All formerly-failed tasks should now be pending (succeeded stays succeeded).
    rows = client.get("/api/status/tasks").json()["tasks"]
    assert not any(t["status"].startswith("failed") for t in rows)
    still_succeeded = [t for t in rows if t["status"] == "succeeded"]
    assert len(still_succeeded) == 1


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
