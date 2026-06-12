from __future__ import annotations

from pathlib import Path
import tomllib


def test_dockerfile_includes_wrapper_scripts() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "COPY scripts ./scripts" in dockerfile
    assert "COPY config ./config" in dockerfile


def test_dockerfile_can_optionally_install_funasr_runtime() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "ARG PCN_INSTALL_FUNASR=false" in dockerfile
    assert 'if [ "$PCN_INSTALL_FUNASR" = "true" ]' in dockerfile
    assert "uv sync --frozen --no-dev --extra funasr" in dockerfile
    assert "uv pip install" not in dockerfile


def test_pyproject_declares_funasr_optional_extra() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["optional-dependencies"]["funasr"] == [
        "funasr>=1.2.0",
        "modelscope>=1.14.0",
    ]


def test_compose_exposes_funasr_build_arg() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "PCN_INSTALL_FUNASR: ${PCN_INSTALL_FUNASR:-false}" in compose


def test_funasr_example_config_enables_real_model_backends() -> None:
    config = Path("config/funasr.example.toml").read_text(encoding="utf-8")

    assert 'data_dir = "/data"' in config
    assert 'obsidian_vault = "/obsidian"' in config
    assert '[vad]\nbackend = "funasr"' in config
    assert '[asr]\nbackend = "funasr"' in config
    assert 'model_id = "iic/SenseVoiceSmall"' in config


def test_runbook_docker_funasr_doctor_uses_funasr_config() -> None:
    runbook = Path("RUNBOOK.md").read_text(encoding="utf-8")

    assert "PCN_INSTALL_FUNASR=true docker compose run --rm personal-context-node doctor --config config/funasr.example.toml" in runbook
