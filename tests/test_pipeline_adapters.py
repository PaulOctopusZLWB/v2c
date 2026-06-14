from __future__ import annotations

import pytest

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.llm.mock import MockLLMAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.config import AppConfig
from personal_context_node.pipeline_adapters import build_asr, build_llm, build_pipeline_adapters, build_vad


def test_build_vad_energy_returns_energy_adapter() -> None:
    assert isinstance(build_vad(vad_backend="energy", vad_command=None, vad_threshold=0.5), EnergyVadAdapter)


def test_build_asr_mock_returns_mock_adapter() -> None:
    assert isinstance(build_asr(asr_backend="mock", asr_command=None, mock_text=None), MockASRAdapter)


def test_build_unknown_backend_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_llm(llm_backend="nope", llm_command=None)


def test_build_pipeline_adapters_uses_config_defaults() -> None:
    # AppConfig defaults to the mock backends, so the assembled adapters reflect them.
    config = AppConfig()
    adapters = build_pipeline_adapters(config=config)
    assert isinstance(adapters.llm, MockLLMAdapter)


def test_command_with_quoted_space_path_is_one_token() -> None:
    # A repo path containing a space (e.g. "v2c 本地部署") must survive command parsing:
    # shlex honours the quotes so the interpreter path stays a single argv token.
    from personal_context_node.adapters.asr.command import CommandASRAdapter

    cmd = '"/Users/x/v2c 本地部署/.venv/bin/python3" scripts/asr.py --language zh'
    adapter = build_asr(asr_backend="command", asr_command=cmd, mock_text=None)
    assert isinstance(adapter, CommandASRAdapter)
    assert adapter.command[0] == "/Users/x/v2c 本地部署/.venv/bin/python3"
    assert adapter.command[1] == "scripts/asr.py"
