from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SpeechRange:
    start_ms: int
    end_ms: int


class VADPort(Protocol):
    def detect(self, audio_path: Path) -> list[SpeechRange]:
        """Return speech ranges in source-audio millisecond offsets."""
