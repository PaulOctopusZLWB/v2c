from __future__ import annotations

import json
import wave
from importlib.resources import files
from pathlib import Path

from personal_context_node.core.ports.asr import ASRResult, ASRSegment


class MockASRAdapter:
    model_name = "mock-asr"
    model_version = "test"

    def __init__(self, *, text: str | None = None, language: str | None = None, model_name: str | None = None) -> None:
        fixture = _mock_asr_fixture()
        self.text = text if text is not None else str(fixture["text"])
        self.language = language if language is not None else str(fixture["language"])
        if model_name is not None:
            self.model_name = model_name

    def transcribe(self, audio_path: Path) -> ASRResult:
        with wave.open(str(audio_path), "rb") as wav:
            duration_ms = round(wav.getnframes() / wav.getframerate() * 1000)
        if duration_ms <= 0:
            segments: list[ASRSegment] = []
        else:
            segments = [ASRSegment(text=self.text, start_ms=0, end_ms=duration_ms, confidence=1.0, language=self.language)]
        return ASRResult(
            segments=segments,
            backend=self.__class__.__name__,
            model_name=self.model_name,
            model_version=self.model_version,
            language=self.language,
            decode_config={"language": self.language, "text": self.text},
            warnings=[],
        )


def _mock_asr_fixture() -> dict[str, object]:
    fixture_path = files("personal_context_node").joinpath("fixtures/mock_asr_transcript.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))
