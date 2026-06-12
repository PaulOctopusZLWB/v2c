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
        config.database_path.parent,
        config.raw_audio_dir,
        config.work_audio_dir,
        config.signing_key_path.parent,
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
            f'raw_audio_dir = "{config.raw_audio_dir}"',
            f'work_audio_dir = "{config.work_audio_dir}"',
            f'sqlite_path = "{config.database_path}"',
            f'obsidian_vault = "{config.obsidian_vault}"',
            f'nas_archive_root = "{config.nas_archive_root}"',
            f'identity_dir = "{config.identity_dir}"',
            "",
            "[identity]",
            f'owner_did = "{config.owner_did}"',
            f'signing_key_path = "{config.signing_key_path}"',
            "",
            "[vad]",
            f'backend = "{config.vad_backend}"',
            f"threshold = {config.vad_threshold}",
            f'model_id = "{config.vad_model_id}"',
            *([] if config.vad_model_revision is None else [f'model_revision = "{config.vad_model_revision}"']),
            f"min_speech_ms = {config.min_speech_ms}",
            f"merge_gap_ms = {config.merge_gap_ms}",
            f"max_chunk_ms = {config.max_chunk_ms}",
            f"chunk_overlap_ms = {config.chunk_overlap_ms}",
            "",
            "[asr]",
            f'backend = "{config.asr_backend}"',
            f'language = "{config.asr_language}"',
            f'model_name = "{config.asr_model_name}"',
            f'model_id = "{config.asr_model_id}"',
            f'model_version = "{config.asr_model_version}"',
            "",
            "[llm]",
            f'backend = "{config.llm_backend}"',
            f"send_person_names = {str(config.send_person_names).lower()}",
            f"send_speaker_labels = {str(config.send_speaker_labels).lower()}",
            f"max_chunk_tokens = {config.max_chunk_tokens}",
            "",
            "[archive]",
            f'backend = "{config.archive_backend}"',
            '# command = "rsync -a {source_path} {archive_path}"',
            "",
            "[device.dji_mic_3]",
            f"enabled = {str(config.dji_mic_3.enabled).lower()}",
            f'volume_root = "{config.dji_mic_3.volume_root}"',
            _optional_toml_path("root_path", config.dji_mic_3.root_path),
            f"volume_name_patterns = {_toml_string_list(config.dji_mic_3.volume_name_patterns)}",
            f"audio_globs = {_toml_string_list(config.dji_mic_3.audio_globs)}",
            f"stable_seconds = {config.dji_mic_3.stable_seconds}",
            "",
            "[audio]",
            f"target_sample_rate_hz = {config.audio.target_sample_rate_hz}",
            f"target_channels = {config.audio.target_channels}",
            f'target_sample_format = "{config.audio.target_sample_format}"',
            "",
        ]
    )


def _optional_toml_path(key: str, value: Path | None) -> str:
    if value is None:
        return f"# {key} = \"/Volumes/DJI_MIC\""
    return f'{key} = "{value}"'


def _toml_string_list(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"
