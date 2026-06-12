from __future__ import annotations

from pathlib import Path


def test_dockerfile_includes_wrapper_scripts() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "COPY scripts ./scripts" in dockerfile


def test_dockerfile_can_optionally_install_funasr_runtime() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "ARG PCN_INSTALL_FUNASR=false" in dockerfile
    assert 'if [ "$PCN_INSTALL_FUNASR" = "true" ]' in dockerfile
    assert "uv pip install --python .venv/bin/python funasr modelscope" in dockerfile


def test_compose_exposes_funasr_build_arg() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "PCN_INSTALL_FUNASR: ${PCN_INSTALL_FUNASR:-false}" in compose
