from __future__ import annotations

from personal_context_node.adapters.vad.command import CommandVADAdapter
from personal_context_node.cli import _build_vad


def test_build_vad_accepts_funasr_backend() -> None:
    adapter = _build_vad(vad_backend="funasr", vad_command=None, vad_threshold=0.03)

    assert isinstance(adapter, CommandVADAdapter)
    assert adapter.command[:2] == ["python3", "scripts/funasr_vad_wrapper.py"]


def test_build_vad_passes_configured_funasr_model_options() -> None:
    adapter = _build_vad(
        vad_backend="funasr",
        vad_command=None,
        vad_threshold=0.03,
        model_id="local-fsmn-vad",
        model_revision="v2.0.4",
    )

    assert isinstance(adapter, CommandVADAdapter)
    assert adapter.command == [
        "python3",
        "scripts/funasr_vad_wrapper.py",
        "--model",
        "local-fsmn-vad",
        "--model-revision",
        "v2.0.4",
    ]


def test_build_vad_allows_funasr_command_override() -> None:
    adapter = _build_vad(vad_backend="funasr", vad_command="uv run python scripts/funasr_vad_wrapper.py --model local-vad", vad_threshold=0.03)

    assert isinstance(adapter, CommandVADAdapter)
    assert adapter.command == ["uv", "run", "python", "scripts/funasr_vad_wrapper.py", "--model", "local-vad"]
