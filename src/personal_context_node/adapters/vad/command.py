from __future__ import annotations

import json
import subprocess
from pathlib import Path

from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError
from personal_context_node.core.ports.vad import SpeechRange, VADResult


class CommandVADAdapter:
    """VAD adapter for local commands or Docker wrapper scripts.

    The wrapper returns raw speech ranges; the configurable merge_gap_ms / min_speech_ms
    (§5, §35) are applied here so VAD tuning is honored for the command/funasr backend.
    """

    def __init__(
        self, *, command: list[str], merge_gap_ms: int = 0, min_speech_ms: int = 0, timeout_seconds: float = 3600.0
    ) -> None:
        self.command = command
        self.merge_gap_ms = merge_gap_ms
        self.min_speech_ms = min_speech_ms
        self.timeout_seconds = timeout_seconds

    def detect(self, audio_path: Path) -> VADResult:
        try:
            result = subprocess.run(
                [*self.command, str(audio_path)],
                check=False,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RetryablePortError(f"VAD command timed out after {self.timeout_seconds:g}s") from exc
        if result.returncode != 0:
            raise RetryablePortError(f"VAD command failed with exit {result.returncode}: {result.stderr.strip()}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise TerminalPortError(f"VAD command emitted invalid JSON: {result.stdout}") from exc
        ranges = payload.get("ranges", payload.get("speech_ranges"))
        if not isinstance(ranges, list):
            raise TerminalPortError("VAD command output must include a ranges list")
        parsed = [_speech_range(item) for item in ranges]
        # Merge adjacent ranges then drop sub-min-speech ones (same order as the energy
        # adapter; §5 "合并相邻语音段"). With both at 0 this is a no-op (raw ranges).
        merged = self._merge(parsed) if self.merge_gap_ms > 0 else parsed
        kept = [r for r in merged if (r.end_ms - r.start_ms) >= self.min_speech_ms] if self.min_speech_ms > 0 else merged
        return VADResult(
            ranges=kept,
            backend=self.__class__.__name__,
            backend_version=None,
            config={"command": self.command, "merge_gap_ms": self.merge_gap_ms, "min_speech_ms": self.min_speech_ms},
            warnings=[],
        )

    def _merge(self, ranges: list[SpeechRange]) -> list[SpeechRange]:
        merged: list[SpeechRange] = []
        for speech_range in ranges:
            if merged and speech_range.start_ms - merged[-1].end_ms <= self.merge_gap_ms:
                merged[-1] = SpeechRange(start_ms=merged[-1].start_ms, end_ms=speech_range.end_ms)
            else:
                merged.append(speech_range)
        return merged


def _speech_range(item: object) -> SpeechRange:
    if not isinstance(item, dict):
        raise TerminalPortError("VAD range must be an object")
    start_ms = int(item["start_ms"])
    end_ms = int(item["end_ms"])
    if end_ms <= start_ms:
        raise TerminalPortError(f"invalid VAD range: start_ms={start_ms} end_ms={end_ms}")
    return SpeechRange(start_ms=start_ms, end_ms=end_ms)
