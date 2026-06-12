from __future__ import annotations

from pathlib import Path
from typing import Any
import tomllib

from pydantic import BaseModel, ConfigDict


class DeviceDiscoveryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    root_path: Path | None = None
    volume_name_patterns: tuple[str, ...] = ("DJI*",)
    audio_globs: tuple[str, ...] = ("**/*.WAV", "**/*.wav")
    stable_seconds: int = 10


class AudioProcessingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_sample_rate_hz: int = 16_000
    target_channels: int = 1
    target_sample_format: str = "s16"


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_dir: Path = Path("data")
    raw_audio_path: Path | None = None
    work_audio_path: Path | None = None
    database_file_path: Path | None = None
    obsidian_vault: Path = Path("/Users/paul/Documents/Obsidian/PersonalContext")
    nas_archive_root: Path = Path("/Volumes/NAS/PersonalContext")
    identity_dir_path: Path | None = None
    source_device: str = "DJI Mic 3"
    owner_did: str = "did:key:local-owner"
    identity_key_path: Path | None = None
    vad_backend: str = "mock"
    vad_threshold: float = 0.03
    vad_model_id: str = "fsmn-vad"
    vad_model_revision: str | None = None
    min_speech_ms: int = 300
    merge_gap_ms: int = 800
    max_chunk_ms: int = 900_000
    chunk_overlap_ms: int = 1_000
    asr_backend: str = "mock"
    asr_command: str | None = None
    asr_language: str = "zh"
    asr_model_name: str = "sensevoice"
    asr_model_id: str = "iic/SenseVoiceSmall"
    asr_model_version: str = "funasr-sensevoice-local"
    llm_backend: str = "mock"
    llm_command: str | None = None
    send_person_names: bool = True
    send_speaker_labels: bool = True
    max_chunk_tokens: int = 6000
    edit_grace_seconds: int = 120
    task_lease_seconds: int = 1800
    task_max_retries: int = 3
    session_gap_minutes: int = 20
    session_cross_midnight_policy: str = "start_date"
    dji_mic_3: DeviceDiscoveryConfig = DeviceDiscoveryConfig()
    audio: AudioProcessingConfig = AudioProcessingConfig()

    @classmethod
    def from_toml(cls, path: Path, **overrides: Any) -> "AppConfig":
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        base_dir = path.parent
        paths = raw.get("paths", {})
        vad = raw.get("vad", {})
        asr = raw.get("asr", {})
        llm = raw.get("llm", {})
        obsidian = raw.get("obsidian", {})
        identity = raw.get("identity", {})
        device = raw.get("device", {})
        dji_mic_3 = device.get("dji_mic_3", {})
        audio = raw.get("audio", {})
        tasks = raw.get("tasks", {})
        session = raw.get("session", {})
        values: dict[str, Any] = {
            "data_dir": _resolve_path(base_dir, paths.get("data_dir", cls.model_fields["data_dir"].default)),
            "raw_audio_path": _optional_resolve_path(base_dir, paths.get("raw_audio_dir")),
            "work_audio_path": _optional_resolve_path(base_dir, paths.get("work_audio_dir")),
            "database_file_path": _optional_resolve_path(base_dir, paths.get("sqlite_path")),
            "obsidian_vault": Path(paths.get("obsidian_vault", cls.model_fields["obsidian_vault"].default)),
            "nas_archive_root": Path(paths.get("nas_archive_root", cls.model_fields["nas_archive_root"].default)),
            "identity_dir_path": _optional_resolve_path(base_dir, paths.get("identity_dir")),
            "owner_did": identity.get("owner_did", cls.model_fields["owner_did"].default),
            "identity_key_path": _optional_resolve_path(base_dir, identity.get("signing_key_path")),
            "vad_backend": vad.get("backend", cls.model_fields["vad_backend"].default),
            "vad_threshold": vad.get("threshold", cls.model_fields["vad_threshold"].default),
            "vad_model_id": vad.get("model_id", cls.model_fields["vad_model_id"].default),
            "vad_model_revision": vad.get("model_revision", cls.model_fields["vad_model_revision"].default),
            "min_speech_ms": vad.get("min_speech_ms", cls.model_fields["min_speech_ms"].default),
            "merge_gap_ms": vad.get("merge_gap_ms", cls.model_fields["merge_gap_ms"].default),
            "max_chunk_ms": vad.get("max_chunk_ms", cls.model_fields["max_chunk_ms"].default),
            "chunk_overlap_ms": vad.get("chunk_overlap_ms", cls.model_fields["chunk_overlap_ms"].default),
            "asr_backend": asr.get("backend", cls.model_fields["asr_backend"].default),
            "asr_command": asr.get("command", cls.model_fields["asr_command"].default),
            "asr_language": asr.get("language", cls.model_fields["asr_language"].default),
            "asr_model_name": asr.get("model_name", cls.model_fields["asr_model_name"].default),
            "asr_model_id": asr.get("model_id", cls.model_fields["asr_model_id"].default),
            "asr_model_version": asr.get("model_version", cls.model_fields["asr_model_version"].default),
            "llm_backend": llm.get("backend", cls.model_fields["llm_backend"].default),
            "llm_command": llm.get("command", cls.model_fields["llm_command"].default),
            "send_person_names": llm.get("send_person_names", cls.model_fields["send_person_names"].default),
            "send_speaker_labels": llm.get("send_speaker_labels", cls.model_fields["send_speaker_labels"].default),
            "max_chunk_tokens": llm.get("max_chunk_tokens", cls.model_fields["max_chunk_tokens"].default),
            "edit_grace_seconds": obsidian.get("edit_grace_seconds", cls.model_fields["edit_grace_seconds"].default),
            "task_lease_seconds": tasks.get("lease_seconds", cls.model_fields["task_lease_seconds"].default),
            "task_max_retries": tasks.get("max_retries", cls.model_fields["task_max_retries"].default),
            "session_gap_minutes": session.get("session_gap_minutes", cls.model_fields["session_gap_minutes"].default),
            "session_cross_midnight_policy": session.get(
                "cross_midnight_policy",
                cls.model_fields["session_cross_midnight_policy"].default,
            ),
            "dji_mic_3": _device_config(base_dir, dji_mic_3),
            "audio": _audio_config(audio),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**values)

    @property
    def database_path(self) -> Path:
        if self.database_file_path is not None:
            return self.database_file_path
        return self.data_dir / "db" / "personal_context.sqlite"

    @property
    def raw_audio_dir(self) -> Path:
        if self.raw_audio_path is not None:
            return self.raw_audio_path
        return self.data_dir / "audio" / "raw"

    @property
    def work_audio_dir(self) -> Path:
        if self.work_audio_path is not None:
            return self.work_audio_path
        return self.data_dir / "audio" / "work"

    @property
    def identity_dir(self) -> Path:
        if self.identity_dir_path is not None:
            return self.identity_dir_path
        return self.data_dir

    @property
    def signing_key_path(self) -> Path:
        if self.identity_key_path is not None:
            if self.identity_key_path.is_absolute():
                return self.identity_key_path
            return self.data_dir / self.identity_key_path
        return self.data_dir / "keys" / "pcn_ed25519.key"


def _resolve_path(base_dir: Path, value: object) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path.resolve(strict=False)
    return (base_dir / path).resolve(strict=False)


def _optional_resolve_path(base_dir: Path, value: object | None) -> Path | None:
    if value is None:
        return None
    return _resolve_path(base_dir, value)


def _device_config(base_dir: Path, raw: dict[str, Any]) -> DeviceDiscoveryConfig:
    return DeviceDiscoveryConfig(
        enabled=raw.get("enabled", DeviceDiscoveryConfig.model_fields["enabled"].default),
        root_path=_optional_resolve_path(base_dir, raw.get("root_path")),
        volume_name_patterns=tuple(raw.get("volume_name_patterns", DeviceDiscoveryConfig.model_fields["volume_name_patterns"].default)),
        audio_globs=tuple(raw.get("audio_globs", DeviceDiscoveryConfig.model_fields["audio_globs"].default)),
        stable_seconds=raw.get("stable_seconds", DeviceDiscoveryConfig.model_fields["stable_seconds"].default),
    )


def _audio_config(raw: dict[str, Any]) -> AudioProcessingConfig:
    return AudioProcessingConfig(
        target_sample_rate_hz=raw.get("target_sample_rate_hz", AudioProcessingConfig.model_fields["target_sample_rate_hz"].default),
        target_channels=raw.get("target_channels", AudioProcessingConfig.model_fields["target_channels"].default),
        target_sample_format=raw.get("target_sample_format", AudioProcessingConfig.model_fields["target_sample_format"].default),
    )
