from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.process_runner import drain_process_queue


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


class _Unused:
    pass
