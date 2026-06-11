from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SpeechRange:
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class VADResult:
    ranges: list[SpeechRange]
    backend: str
    backend_version: str | None = None
    config: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class VADPort(Protocol):
    def detect(self, audio_path: Path) -> VADResult:
        """Return speech ranges in source-audio millisecond offsets."""
