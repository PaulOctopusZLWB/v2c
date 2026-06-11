from __future__ import annotations

from pathlib import Path
from typing import Any
import tomllib

from pydantic import BaseModel, ConfigDict


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_dir: Path = Path("data")
    obsidian_vault: Path = Path("/Users/paul/Documents/Obsidian/PersonalContext")
    nas_archive_root: Path = Path("/Volumes/NAS/PersonalContext")
    source_device: str = "DJI Mic 3"
    owner_did: str = "did:key:local-owner"
    identity_key_path: Path | None = None
    vad_backend: str = "energy"
    vad_threshold: float = 0.03
    max_chunk_ms: int = 30_000
    asr_backend: str = "mock"
    asr_command: str | None = None
    llm_backend: str = "rule_based"
    send_person_names: bool = True
    send_speaker_labels: bool = True
    max_chunk_tokens: int = 6000
    edit_grace_seconds: int = 120

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
        values: dict[str, Any] = {
            "data_dir": _resolve_path(base_dir, paths.get("data_dir", cls.model_fields["data_dir"].default)),
            "obsidian_vault": Path(paths.get("obsidian_vault", cls.model_fields["obsidian_vault"].default)),
            "nas_archive_root": Path(paths.get("nas_archive_root", cls.model_fields["nas_archive_root"].default)),
            "owner_did": identity.get("owner_did", cls.model_fields["owner_did"].default),
            "identity_key_path": _optional_resolve_path(base_dir, identity.get("signing_key_path")),
            "vad_backend": vad.get("backend", cls.model_fields["vad_backend"].default),
            "vad_threshold": vad.get("threshold", cls.model_fields["vad_threshold"].default),
            "max_chunk_ms": vad.get("max_chunk_ms", cls.model_fields["max_chunk_ms"].default),
            "asr_backend": asr.get("backend", cls.model_fields["asr_backend"].default),
            "asr_command": asr.get("command", cls.model_fields["asr_command"].default),
            "llm_backend": llm.get("backend", cls.model_fields["llm_backend"].default),
            "send_person_names": llm.get("send_person_names", cls.model_fields["send_person_names"].default),
            "send_speaker_labels": llm.get("send_speaker_labels", cls.model_fields["send_speaker_labels"].default),
            "max_chunk_tokens": llm.get("max_chunk_tokens", cls.model_fields["max_chunk_tokens"].default),
            "edit_grace_seconds": obsidian.get("edit_grace_seconds", cls.model_fields["edit_grace_seconds"].default),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**values)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "db" / "personal_context.sqlite"

    @property
    def raw_audio_dir(self) -> Path:
        return self.data_dir / "audio" / "raw"

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
        return path
    return base_dir / path


def _optional_resolve_path(base_dir: Path, value: object | None) -> Path | None:
    if value is None:
        return None
    return _resolve_path(base_dir, value)
