from __future__ import annotations

import threading
import time
from pathlib import Path

import personal_context_node.process_runner as _pr_module
from personal_context_node.config import AppConfig
from personal_context_node.process_runner import drain_process_queue
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.tasks import enqueue_task


class _UnusedPort:
    pass


class _OverlapTracker:
    """Thread-safe call tracker recording the peak number of concurrent executions."""

    def __init__(self, sleep_seconds: float) -> None:
        self._lock = threading.Lock()
        self._sleep = sleep_seconds
        self.active = 0
        self.max_active = 0
        self.calls = 0

    def __call__(self, **kwargs) -> None:
        with self._lock:
            self.active += 1
            self.calls += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self._sleep)
        with self._lock:
            self.active -= 1


def _config(tmp_path: Path, workers: int = 3) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path / "data",
        obsidian_vault=tmp_path / "vault",
        pipeline_workers=workers,
    )


def test_concurrent_drain_runs_cpu_tasks_in_parallel(tmp_path: Path, monkeypatch) -> None:
    # 3 workers = 1 GPU + 2 CPU threads; four archive tasks must overlap on the CPU threads.
    config = _config(tmp_path)
    for i in range(4):
        enqueue_task(config=config, task_type="archive", target_type="all", target_id=f"t{i}")

    tracker = _OverlapTracker(sleep_seconds=0.3)
    monkeypatch.setattr(_pr_module, "archive_completed_audio", tracker)
    monkeypatch.setattr(_pr_module, "build_archive_adapter", lambda **kwargs: _UnusedPort())

    result = drain_process_queue(config=config, vad=_UnusedPort(), asr=_UnusedPort())

    assert result.status == "complete"
    assert result.tasks_succeeded == 4
    assert result.tasks_failed == 0
    assert tracker.calls == 4
    assert tracker.max_active >= 2


def test_concurrent_drain_serializes_gpu_tasks(tmp_path: Path, monkeypatch) -> None:
    # GPU stages (vad/asr/transcribe_diarize) are pinned to one thread: the resident-model
    # adapters are single-subprocess and must never see overlapping calls.
    config = _config(tmp_path)
    for i in range(3):
        enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id=f"aud{i}")

    tracker = _OverlapTracker(sleep_seconds=0.2)
    monkeypatch.setattr(_pr_module, "preprocess_imported_audio", tracker)

    result = drain_process_queue(config=config, vad=_UnusedPort(), asr=_UnusedPort())

    assert result.status == "complete"
    assert result.tasks_succeeded == 3
    assert tracker.calls == 3
    assert tracker.max_active == 1


def test_concurrent_drain_picks_up_fanned_out_downstream_work(tmp_path: Path, monkeypatch) -> None:
    # While a vad task is executing, the CPU threads see an empty queue — they must NOT
    # exit the drain before the vad fan-out (vad -> asr) lands, and the drain as a whole
    # must finish both tasks in one call.
    config = _config(tmp_path)
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into audio_files (
              audio_file_id, source_device, source_path, local_raw_path, sha256,
              duration_ms, recorded_at, imported_at, status
            ) values ('aud1', 'DJI Mic 3', '/src/a.wav', '/raw/a.wav', 'deadbeef',
                      1000, '2026-07-09T10:00:00+00:00', '2026-07-09T11:00:00+00:00', 'imported')
            """
        )
        conn.execute(
            """
            insert into audio_chunks (
              chunk_id, audio_file_id, source_start_ms, source_end_ms, local_chunk_path, status
            ) values ('chunk1', 'aud1', 0, 1000, '/work/chunk1.wav', 'pending_asr')
            """
        )
        conn.commit()
    finally:
        conn.close()
    enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="aud1")

    def slow_vad(**kwargs) -> None:
        time.sleep(0.6)  # long enough for idle CPU threads to hit their exit check twice

    asr_calls = {"n": 0}

    def fake_asr(**kwargs) -> None:
        asr_calls["n"] += 1

    monkeypatch.setattr(_pr_module, "preprocess_imported_audio", slow_vad)
    monkeypatch.setattr(_pr_module, "transcribe_pending_chunks", fake_asr)

    result = drain_process_queue(config=config, vad=_UnusedPort(), asr=_UnusedPort())

    assert result.status == "complete"
    # vad + the asr task it fanned out + the extract_features leaf asr fanned out (which
    # no-ops: the chunk has no transcript segments, so its pending scope is empty).
    assert result.tasks_succeeded == 3
    assert asr_calls["n"] == 1


def test_concurrent_drain_isolates_task_failures(tmp_path: Path, monkeypatch) -> None:
    # One stage blowing up must not abort the drain; independent tasks still complete.
    config = _config(tmp_path)
    enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="boom")
    for i in range(2):
        enqueue_task(config=config, task_type="archive", target_type="all", target_id=f"t{i}")

    def failing_vad(**kwargs) -> None:
        raise RuntimeError("vad exploded")

    monkeypatch.setattr(_pr_module, "preprocess_imported_audio", failing_vad)
    monkeypatch.setattr(_pr_module, "archive_completed_audio", lambda **kwargs: None)
    monkeypatch.setattr(_pr_module, "build_archive_adapter", lambda **kwargs: _UnusedPort())

    result = drain_process_queue(config=config, vad=_UnusedPort(), asr=_UnusedPort())

    assert result.tasks_succeeded == 2
    assert result.tasks_failed >= 1


def test_single_worker_config_uses_sequential_drain(tmp_path: Path, monkeypatch) -> None:
    # pipeline_workers=1 (the default) must keep the historical single-threaded behavior.
    config = _config(tmp_path, workers=1)
    for i in range(2):
        enqueue_task(config=config, task_type="archive", target_type="all", target_id=f"t{i}")

    tracker = _OverlapTracker(sleep_seconds=0.05)
    monkeypatch.setattr(_pr_module, "archive_completed_audio", tracker)
    monkeypatch.setattr(_pr_module, "build_archive_adapter", lambda **kwargs: _UnusedPort())

    result = drain_process_queue(config=config, vad=_UnusedPort(), asr=_UnusedPort())

    assert result.status == "complete"
    assert result.tasks_succeeded == 2
    assert tracker.max_active == 1
