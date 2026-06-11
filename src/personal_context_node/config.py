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
    vad_backend: str = "energy"
    vad_threshold: float = 0.03
    max_chunk_ms: int = 30_000
    asr_backend: str = "mock"
    asr_command: str | None = None
    llm_backend: str = "rule_based"

    @classmethod
    def from_toml(cls, path: Path, **overrides: Any) -> "AppConfig":
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        base_dir = path.parent
        paths = raw.get("paths", {})
        vad = raw.get("vad", {})
        asr = raw.get("asr", {})
        llm = raw.get("llm", {})
        values: dict[str, Any] = {
            "data_dir": _resolve_path(base_dir, paths.get("data_dir", cls.model_fields["data_dir"].default)),
            "obsidian_vault": Path(paths.get("obsidian_vault", cls.model_fields["obsidian_vault"].default)),
            "nas_archive_root": Path(paths.get("nas_archive_root", cls.model_fields["nas_archive_root"].default)),
            "vad_backend": vad.get("backend", cls.model_fields["vad_backend"].default),
            "vad_threshold": vad.get("threshold", cls.model_fields["vad_threshold"].default),
            "max_chunk_ms": vad.get("max_chunk_ms", cls.model_fields["max_chunk_ms"].default),
            "asr_backend": asr.get("backend", cls.model_fields["asr_backend"].default),
            "asr_command": asr.get("command", cls.model_fields["asr_command"].default),
            "llm_backend": llm.get("backend", cls.model_fields["llm_backend"].default),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})
        return cls(**values)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "db" / "personal_context.sqlite"

    @property
    def raw_audio_dir(self) -> Path:
        return self.data_dir / "audio" / "raw"


def _resolve_path(base_dir: Path, value: object) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path
    return base_dir / path
