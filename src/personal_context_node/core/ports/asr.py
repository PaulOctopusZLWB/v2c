from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ASRSegment:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None
    language: str = "zh"


class ASRPort(Protocol):
    model_name: str
    model_version: str

    def transcribe(self, audio_path: Path) -> list[ASRSegment]:
        """Return segments relative to the provided chunk path."""
