from __future__ import annotations

import wave
from pathlib import Path

from personal_context_node.core.ports.asr import ASRSegment


class MockASRAdapter:
    model_name = "mock-asr"
    model_version = "test"

    def __init__(self, *, text: str = "模拟本地转写") -> None:
        self.text = text

    def transcribe(self, audio_path: Path) -> list[ASRSegment]:
        with wave.open(str(audio_path), "rb") as wav:
            duration_ms = round(wav.getnframes() / wav.getframerate() * 1000)
        if duration_ms <= 0:
            return []
        return [ASRSegment(text=self.text, start_ms=0, end_ms=duration_ms, confidence=1.0)]
