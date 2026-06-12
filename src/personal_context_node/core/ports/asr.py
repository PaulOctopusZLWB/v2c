from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ASRSegment:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None
    language: str = "zh"
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ASRResult:
    segments: list[ASRSegment]
    backend: str
    model_name: str
    model_version: str | None = None
    language: str | None = None
    decode_config: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class ASRPort(Protocol):
    model_name: str
    model_version: str

    def transcribe(self, audio_path: Path) -> ASRResult:
        """Return segments relative to the provided chunk path."""
