from __future__ import annotations

import json
import subprocess
from pathlib import Path

from personal_context_node.core.ports.vad import SpeechRange


class CommandVADAdapter:
    """VAD adapter for local commands or Docker wrapper scripts."""

    def __init__(self, *, command: list[str]) -> None:
        self.command = command

    def detect(self, audio_path: Path) -> list[SpeechRange]:
        result = subprocess.run(
            [*self.command, str(audio_path)],
            check=True,
            text=True,
            capture_output=True,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(f"VAD command emitted invalid JSON: {result.stdout}") from exc
        ranges = payload.get("ranges", payload.get("speech_ranges"))
        if not isinstance(ranges, list):
            raise ValueError("VAD command output must include a ranges list")
        return [_speech_range(item) for item in ranges]


def _speech_range(item: object) -> SpeechRange:
    if not isinstance(item, dict):
        raise ValueError("VAD range must be an object")
    start_ms = int(item["start_ms"])
    end_ms = int(item["end_ms"])
    if end_ms <= start_ms:
        raise ValueError(f"invalid VAD range: start_ms={start_ms} end_ms={end_ms}")
    return SpeechRange(start_ms=start_ms, end_ms=end_ms)
