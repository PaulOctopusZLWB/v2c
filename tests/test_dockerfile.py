from __future__ import annotations

from pathlib import Path
import tomllib


def test_dockerignore_excludes_local_runtime_data() -> None:
    ignored = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    for required in [".venv/", ".tmp/", "data/", "sample_data/", "web/node_modules/", "web/dist/", ".pytest_cache/", ".ruff_cache/"]:
        assert required in ignored


def test_dockerignore_excludes_config_relative_runtime_data() -> None:
    # `COPY config ./config` would otherwise bake config/data (live SQLite DB, raw audio,
    # signing key) into the image, since data_dir resolves relative to the config file.
    ignored = Path(".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "config/data/" in ignored


def test_dockerfile_includes_wrapper_scripts() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "COPY scripts ./scripts" in dockerfile
    assert "COPY config ./config" in dockerfile
    assert "COPY --from=web-build /app/web/dist ./web/dist" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm run build" in dockerfile


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
        "torch>=2.2.0",
        "torchaudio>=2.2.0",
    ]


def test_compose_exposes_funasr_build_arg() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "PCN_INSTALL_FUNASR: ${PCN_INSTALL_FUNASR:-false}" in compose


def test_compose_runs_web_with_persistent_portable_mounts() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "/app/config/deploy.toml" in compose
    assert "${PCN_CONFIG_FILE:-./config/remote.example.toml}:/app/config/deploy.toml:ro" in compose
    assert '"127.0.0.1:${PCN_WEB_PORT:-8765}:8765"' in compose
    assert "${PCN_INPUT_DIR:-./runtime/input}:/input:ro" in compose
    assert "${PCN_DATA_DIR:-./runtime/data}:/data" in compose
    assert "/Users/" not in compose


def test_remote_example_uses_container_paths_and_safe_defaults() -> None:
    config = Path("config/remote.example.toml").read_text(encoding="utf-8")

    assert 'data_dir = "/data"' in config
    assert 'obsidian_vault = "/obsidian"' in config
    assert 'root_path = "/input"' in config
    assert '[asr]\nbackend = "mock"' in config
    assert '[llm]\nbackend = "rule_based"' in config


def test_funasr_example_config_enables_real_model_backends() -> None:
    config = Path("config/funasr.example.toml").read_text(encoding="utf-8")

    assert 'data_dir = "/data"' in config
    assert 'obsidian_vault = "/obsidian"' in config
    assert '[vad]\nbackend = "funasr"' in config
    # Anchor the ASR backend to its own section header: a bare 'backend = "funasr"' substring
    # would also match the [vad] section above, so the section-prefixed form is required.
    assert '[asr]\nbackend = "funasr"' in config or '[asr]\nbackend = "funasr_server"' in config
    # Standard path is whole-file diarization (chunk/SenseVoice is deprecated).
    assert 'mode = "diarize"' in config
    assert 'diarize_model = "paraformer-zh"' in config


def test_runbook_docker_funasr_doctor_uses_funasr_config() -> None:
    runbook = Path("RUNBOOK.md").read_text(encoding="utf-8")

    assert "PCN_INSTALL_FUNASR=true docker compose run --rm personal-context-node doctor --config config/funasr.example.toml" in runbook
