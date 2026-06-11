from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize


VAULT_DIRS = [
    "00_Inbox",
    "10_Daily",
    "20_Conversations",
    "30_Memory_Candidates",
    "40_Confirmed_Memory",
    "90_System",
]


@dataclass(frozen=True)
class InitResult:
    initialized: bool
    config_path: Path | None


@dataclass(frozen=True)
class HealthResult:
    status: str
    database: str
    obsidian_vault: str


def initialize_workspace(*, config: AppConfig, config_path: Path | None = None) -> InitResult:
    for directory in [
        config.data_dir / "db",
        config.data_dir / "audio" / "raw",
        config.data_dir / "audio" / "work",
        config.data_dir / "logs",
    ]:
        directory.mkdir(parents=True, exist_ok=True)
    for folder in VAULT_DIRS:
        (config.obsidian_vault / folder).mkdir(parents=True, exist_ok=True)
    conn = connect(config.database_path)
    try:
        initialize(conn)
    finally:
        conn.close()
    if config_path is not None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if not config_path.exists():
            config_path.write_text(_config_text(config), encoding="utf-8")
    return InitResult(initialized=True, config_path=config_path)


def check_health(*, config: AppConfig) -> HealthResult:
    database = "ok"
    obsidian_vault = "ok"
    try:
        conn = connect(config.database_path)
        try:
            initialize(conn)
            conn.execute("select 1").fetchone()
        finally:
            conn.close()
    except Exception:
        database = "error"
    try:
        config.obsidian_vault.mkdir(parents=True, exist_ok=True)
        probe = config.obsidian_vault / ".pcn-healthcheck"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception:
        obsidian_vault = "error"
    status = "ok" if database == "ok" and obsidian_vault == "ok" else "error"
    return HealthResult(status=status, database=database, obsidian_vault=obsidian_vault)


def _config_text(config: AppConfig) -> str:
    return "\n".join(
        [
            "[paths]",
            f'data_dir = "{config.data_dir}"',
            f'obsidian_vault = "{config.obsidian_vault}"',
            f'nas_archive_root = "{config.nas_archive_root}"',
            "",
            "[vad]",
            f'backend = "{config.vad_backend}"',
            f"threshold = {config.vad_threshold}",
            f"max_chunk_ms = {config.max_chunk_ms}",
            "",
            "[asr]",
            f'backend = "{config.asr_backend}"',
            "",
            "[llm]",
            f'backend = "{config.llm_backend}"',
            f"send_person_names = {str(config.send_person_names).lower()}",
            f"send_speaker_labels = {str(config.send_speaker_labels).lower()}",
            f"max_chunk_tokens = {config.max_chunk_tokens}",
            "",
        ]
    )
