from __future__ import annotations

import json
import subprocess
from pathlib import Path

from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError
from personal_context_node.core.ports.vad import SpeechRange, VADResult


class CommandVADAdapter:
    """VAD adapter for local commands or Docker wrapper scripts."""

    def __init__(self, *, command: list[str]) -> None:
        self.command = command

    def detect(self, audio_path: Path) -> VADResult:
        result = subprocess.run(
            [*self.command, str(audio_path)],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RetryablePortError(f"VAD command failed with exit {result.returncode}: {result.stderr.strip()}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise TerminalPortError(f"VAD command emitted invalid JSON: {result.stdout}") from exc
        ranges = payload.get("ranges", payload.get("speech_ranges"))
        if not isinstance(ranges, list):
            raise TerminalPortError("VAD command output must include a ranges list")
        return VADResult(
            ranges=[_speech_range(item) for item in ranges],
            backend=self.__class__.__name__,
            backend_version=None,
            config={"command": self.command},
            warnings=[],
        )


def _speech_range(item: object) -> SpeechRange:
    if not isinstance(item, dict):
        raise TerminalPortError("VAD range must be an object")
    start_ms = int(item["start_ms"])
    end_ms = int(item["end_ms"])
    if end_ms <= start_ms:
        raise TerminalPortError(f"invalid VAD range: start_ms={start_ms} end_ms={end_ms}")
    return SpeechRange(start_ms=start_ms, end_ms=end_ms)
