from __future__ import annotations

import wave
from pathlib import Path

from personal_context_node.core.ports.asr import ASRResult, ASRSegment


class MockASRAdapter:
    model_name = "mock-asr"
    model_version = "test"

    def __init__(self, *, text: str = "模拟本地转写") -> None:
        self.text = text

    def transcribe(self, audio_path: Path) -> ASRResult:
        with wave.open(str(audio_path), "rb") as wav:
            duration_ms = round(wav.getnframes() / wav.getframerate() * 1000)
        if duration_ms <= 0:
            segments: list[ASRSegment] = []
        else:
            segments = [ASRSegment(text=self.text, start_ms=0, end_ms=duration_ms, confidence=1.0)]
        return ASRResult(
            segments=segments,
            backend=self.__class__.__name__,
            model_name=self.model_name,
            model_version=self.model_version,
            language="zh",
            decode_config={"text": self.text},
            warnings=[],
        )
