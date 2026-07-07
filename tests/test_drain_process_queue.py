from __future__ import annotations

from pathlib import Path

from personal_context_node import process_runner as _pr_module
from personal_context_node.adapters.asr.persistent_command import PersistentCommandASRAdapter
from personal_context_node.config import AppConfig
from personal_context_node.pipeline_adapters import PipelineAdapters
from personal_context_node.process_runner import ProcessOnceResult, drain_process_queue
from personal_context_node.web.worker import PipelineWorker


def test_drain_empty_queue_reports_complete(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    result = drain_process_queue(config=config, vad=_Unused(), asr=_Unused(), llm=_Unused())
    assert result.status == "complete"
    assert result.process_steps == 0


def test_drain_stops_when_should_stop_true(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    result = drain_process_queue(config=config, vad=_Unused(), asr=_Unused(), llm=_Unused(), should_stop=lambda: True)
    assert result.status == "stopped"
    assert result.process_steps == 0


def test_web_worker_drains_more_than_default_max_steps(tmp_path: Path, monkeypatch) -> None:
    # A backlog larger than the old 200-step cap must drain in a single worker run.
    # We simulate 205 tasks by patching process_once to return "succeeded" 205 times,
    # then "no_task" — and assert the worker processes all 205 (not just 200).
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    total_tasks = 205
    calls: dict[str, int] = {"n": 0}

    def fake_process_once(**kwargs) -> ProcessOnceResult:
        calls["n"] += 1
        if calls["n"] <= total_tasks:
            return ProcessOnceResult(task_id=f"t{calls['n']}", task_type="asr", status="succeeded")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    def fake_preview(**kwargs) -> ProcessOnceResult:
        # Return dry_run (work pending) until all tasks have been consumed.
        if calls["n"] < total_tasks:
            return ProcessOnceResult(task_id="peek", task_type="asr", status="dry_run")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    monkeypatch.setattr(_pr_module, "process_once", fake_process_once)
    monkeypatch.setattr(_pr_module, "preview_next_process_task", fake_preview)

    worker = PipelineWorker(config=config)
    result = worker.drain_now()

    assert result.status == "complete"
    assert result.tasks_succeeded == total_tasks


def test_web_worker_import_path_drains_more_than_default_max_steps(tmp_path: Path, monkeypatch) -> None:
    # The default non-blocking UI import path (start_import -> _import_then_drain) must ALSO
    # drain past the 200-step cap, not just the explicit /run path.
    import personal_context_node.web.worker as _worker_module

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    total_tasks = 205
    calls: dict[str, int] = {"n": 0}

    def fake_process_once(**kwargs) -> ProcessOnceResult:
        calls["n"] += 1
        if calls["n"] <= total_tasks:
            return ProcessOnceResult(task_id=f"t{calls['n']}", task_type="asr", status="succeeded")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    def fake_preview(**kwargs) -> ProcessOnceResult:
        if calls["n"] < total_tasks:
            return ProcessOnceResult(task_id="peek", task_type="asr", status="dry_run")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    monkeypatch.setattr(_pr_module, "process_once", fake_process_once)
    monkeypatch.setattr(_pr_module, "preview_next_process_task", fake_preview)
    monkeypatch.setattr(_worker_module, "import_audio_files", lambda **kwargs: None)

    worker = PipelineWorker(config=config)
    assert worker.start_import("ignored") is True
    worker._thread.join(timeout=30)

    assert worker._last_result is not None
    assert worker._last_result.tasks_succeeded == total_tasks


def test_web_worker_import_failure_still_drains_existing_queue(tmp_path: Path, monkeypatch) -> None:
    # Directory import can fail after some files were already registered by another ingest path.
    # The background UI worker must still drain whatever is already pending instead of leaving
    # the header stuck on pending tasks until someone manually presses Run.
    import personal_context_node.web.worker as _worker_module

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    calls: dict[str, int] = {"n": 0}

    def fake_process_once(**kwargs) -> ProcessOnceResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return ProcessOnceResult(task_id="t1", task_type="transcribe_diarize", status="succeeded")
        return ProcessOnceResult(task_id=None, task_type=None, status="no_task")

    monkeypatch.setattr(_pr_module, "process_once", fake_process_once)
    monkeypatch.setattr(
        _worker_module,
        "import_audio_files",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("import failed after partial progress")),
    )

    worker = PipelineWorker(config=config)
    assert worker.start_import("ignored") is True
    worker._thread.join(timeout=30)

    assert worker._last_result is not None
    assert worker._last_result.status == "complete"
    assert worker._last_result.tasks_succeeded == 1


def test_drain_closes_persistent_asr_adapter_when_done(tmp_path: Path, monkeypatch) -> None:
    # A funasr_server drain owns a resident model subprocess; once drain_now() returns the
    # worker must close() it (try/finally), or every import leaks a live MPS process. We prove
    # the actual subprocess is reaped — deterministically, without forcing GC.
    import personal_context_node.web.worker as _worker_module

    server = tmp_path / "resident.py"
    server.write_text("import sys\nfor _line in sys.stdin:\n    pass\n", encoding="utf-8")
    adapter = PersistentCommandASRAdapter(command=["python3", str(server)], timeout_seconds=10)
    proc = adapter._ensure()  # spawn the resident process up front
    assert proc.poll() is None  # alive before the drain

    monkeypatch.setattr(
        _worker_module, "build_pipeline_adapters",
        lambda **kwargs: PipelineAdapters(vad=_Unused(), asr=adapter, llm=_Unused()),
    )

    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    worker = PipelineWorker(config=config)
    result = worker.drain_now()  # empty queue -> returns immediately, then closes adapters

    assert result.status == "complete"
    assert adapter._proc is None  # adapter forgot its process (close() ran)
    assert proc.poll() is not None  # the resident subprocess was actually terminated/reaped


class _Unused:
    pass
