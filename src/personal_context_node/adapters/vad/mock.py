from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from personal_context_node.core.ports.vad import SpeechRange, VADResult


class MockVADAdapter:
    """Fixture-backed deterministic VAD adapter for E2E and CLI smoke tests."""

    def __init__(self, *, fixture: dict[str, object] | None = None) -> None:
        self.fixture = fixture or _mock_vad_fixture()

    def detect(self, audio_path: Path) -> VADResult:
        return VADResult(
            ranges=[
                SpeechRange(start_ms=int(item["start_ms"]), end_ms=int(item["end_ms"]))
                for item in (_range_dict(raw) for raw in _range_list(self.fixture.get("ranges")))
            ],
            backend=self.__class__.__name__,
            backend_version=None,
            config={"fixture": "mock_vad.json"},
            warnings=[],
        )


def _mock_vad_fixture() -> dict[str, object]:
    fixture_path = files("personal_context_node").joinpath("fixtures/mock_vad.json")
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _range_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("mock VAD fixture ranges must be a list")
    return value


def _range_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("mock VAD fixture range must be an object")
    return value
