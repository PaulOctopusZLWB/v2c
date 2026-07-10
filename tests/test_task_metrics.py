from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.tasks import enqueue_task, task_metrics
from personal_context_node.web.app import create_app


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")


def _finish_task(config: AppConfig, target_id: str, *, status: str, duration_ms: int) -> None:
    started = "2026-07-09T10:00:00+00:00"
    finished = f"2026-07-09T10:00:{duration_ms // 1000:02d}.{duration_ms % 1000:03d}000+00:00"
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "update tasks set status = ?, started_at = ?, finished_at = ? where target_id = ?",
            (status, started, finished, target_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_task_metrics_aggregates_counts_and_percentiles(tmp_path: Path) -> None:
    config = _config(tmp_path)
    durations = [100, 200, 1000]
    for i, duration in enumerate(durations):
        enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id=f"c{i}")
        _finish_task(config, f"c{i}", status="succeeded", duration_ms=duration)
    enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="cfail")
    _finish_task(config, "cfail", status="failed_terminal", duration_ms=500)
    enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="pending1")

    metrics = task_metrics(config=config)
    by_type = {entry["task_type"]: entry for entry in metrics["task_types"]}

    asr = by_type["asr"]
    assert asr["counts"] == {"succeeded": 3, "failed_terminal": 1}
    assert asr["total"] == 4
    assert asr["success_rate"] == 0.75
    assert asr["duration_ms"]["count"] == 4
    assert asr["duration_ms"]["p50"] in (200, 500)  # 100,200,500,1000 -> median bucket
    assert asr["duration_ms"]["max"] == 1000

    vad = by_type["vad"]
    assert vad["counts"] == {"pending": 1}
    assert vad["success_rate"] is None
    assert vad["duration_ms"]["count"] == 0
    assert vad["duration_ms"]["p50"] is None


def test_pipeline_metrics_endpoint(tmp_path: Path) -> None:
    config = _config(tmp_path)
    enqueue_task(config=config, task_type="asr", target_type="audio_chunk", target_id="c1")
    _finish_task(config, "c1", status="succeeded", duration_ms=250)

    client = TestClient(create_app(config=config))
    response = client.get("/api/pipeline/metrics")

    assert response.status_code == 200
    body = response.json()
    assert "task_types" in body and "generated_at" in body
    asr = next(entry for entry in body["task_types"] if entry["task_type"] == "asr")
    assert asr["duration_ms"]["p50"] == 250
