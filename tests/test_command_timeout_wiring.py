from __future__ import annotations

from personal_context_node.archive_adapters import build_archive_adapter
from personal_context_node.cli import _build_asr, _build_llm, _build_vad
from personal_context_node.config import AppConfig
from personal_context_node.pipeline_adapters import build_pipeline_adapters


def test_cli_build_vad_threads_command_timeout() -> None:
    adapter = _build_vad(vad_backend="command", vad_command="/bin/echo a", vad_threshold=0.03, timeout_seconds=42.0)

    assert adapter.timeout_seconds == 42.0


def test_cli_build_asr_threads_command_timeout() -> None:
    adapter = _build_asr(asr_backend="command", asr_command="/bin/echo a", mock_text=None, timeout_seconds=42.0)

    assert adapter.timeout_seconds == 42.0


def test_cli_build_llm_threads_command_timeout() -> None:
    adapter = _build_llm(llm_backend="command", llm_command="/bin/echo a", timeout_seconds=42.0)

    assert adapter.timeout_seconds == 42.0


def test_pipeline_adapters_thread_configured_command_timeout() -> None:
    config = AppConfig(
        vad_backend="command",
        vad_command="/bin/echo a",
        asr_backend="command",
        asr_command="/bin/echo a",
        llm_backend="command",
        llm_command="/bin/echo a",
        command_timeout_seconds=42.0,
    )

    adapters = build_pipeline_adapters(config=config)

    assert adapters.vad.timeout_seconds == 42.0
    assert adapters.asr.timeout_seconds == 42.0
    assert adapters.llm.timeout_seconds == 42.0


def test_build_archive_adapter_threads_configured_command_timeout() -> None:
    config = AppConfig(archive_backend="command", archive_command="rsync -a", command_timeout_seconds=42.0)

    adapter = build_archive_adapter(config=config)

    assert adapter.timeout_seconds == 42.0
