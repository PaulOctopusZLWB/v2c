from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_dir: Path = Path("data")
    obsidian_vault: Path = Path("/Users/paul/Documents/Obsidian/PersonalContext")
    source_device: str = "DJI Mic 3"
    owner_did: str = "did:key:local-owner"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "db" / "personal_context.sqlite"

    @property
    def raw_audio_dir(self) -> Path:
        return self.data_dir / "audio" / "raw"
