from __future__ import annotations

import json
import wave
from pathlib import Path

from personal_context_node.adapters.vad.mock import MockVADAdapter
from personal_context_node.cli import _build_vad


def test_mock_vad_ranges_come_from_fixture(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    _write_tiny_wav(audio_path)
    fixture = json.loads(Path("src/personal_context_node/fixtures/mock_vad.json").read_text(encoding="utf-8"))

    result = MockVADAdapter().detect(audio_path)

    assert [range_.__dict__ for range_ in result.ranges] == fixture["ranges"]
    assert result.backend == "MockVADAdapter"
    assert result.config == {"fixture": "mock_vad.json"}


def test_build_vad_accepts_mock_backend() -> None:
    adapter = _build_vad(vad_backend="mock", vad_command=None, vad_threshold=0.03)

    assert isinstance(adapter, MockVADAdapter)


def _write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\1" * 16_000)
