from __future__ import annotations

from pathlib import Path

from personal_context_node import process_runner as _pr_module
from personal_context_node.config import AppConfig
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


class _Unused:
    pass
