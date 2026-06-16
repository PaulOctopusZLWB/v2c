from __future__ import annotations

import json
import subprocess
from pathlib import Path

from personal_context_node.adapters.command_runner import run_command
from personal_context_node.core.ports.asr import ASRResult, ASRSegment
from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError


# Exit-code contract for ASR wrapper commands (§28.3.4): a permanently-unsupported
# input (missing/corrupt/unsupported audio) exits with TERMINAL_EXIT_CODE so the task
# fails terminally; any other non-zero exit (model load failure, transient error) is
# retryable.
TERMINAL_EXIT_CODE = 3


class CommandASRAdapter:
    """ASR adapter for local commands or Docker wrapper scripts.

    The command is invoked as: `<command...> <audio_path>`.
    It must emit JSON to stdout:
    `{"model_name": "...", "model_version": "...", "segments": [...]}`.

    Exit codes: 0 success; ``TERMINAL_EXIT_CODE`` (3) means permanently unsupported
    input (-> TerminalPortError); any other non-zero is retryable.
    """

    def __init__(self, *, command: list[str], timeout_seconds: float = 3600.0) -> None:
        if not command:
            raise ValueError("ASR command must not be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.model_name = "command-asr"
        self.model_version = "unknown"

    def transcribe(self, audio_path: Path) -> ASRResult:
        try:
            completed = run_command([*self.command, str(audio_path)], timeout_seconds=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise RetryablePortError(f"ASR command timed out after {self.timeout_seconds:g}s") from exc
        if completed.returncode == TERMINAL_EXIT_CODE:
            raise TerminalPortError(
                f"ASR command rejected input as permanently unsupported: {completed.stderr.strip()}"
            )
        if completed.returncode != 0:
            raise RetryablePortError(f"ASR command failed with exit {completed.returncode}: {completed.stderr.strip()}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise TerminalPortError(f"invalid ASR JSON: {exc}") from exc
        self.model_name = str(payload.get("model_name", self.model_name))
        self.model_version = str(payload.get("model_version", self.model_version))
        return ASRResult(
            segments=[_asr_segment(segment) for segment in payload.get("segments", [])],
            backend=self.__class__.__name__,
            model_name=self.model_name,
            model_version=self.model_version,
            language=payload.get("language"),
            decode_config={"command": self.command},
            warnings=[str(item) for item in payload.get("warnings", [])],
        )


def _asr_segment(segment: object) -> ASRSegment:
    if not isinstance(segment, dict):
        raise TerminalPortError("ASR segment must be an object")
    return ASRSegment(
        text=str(segment["text"]),
        start_ms=int(segment["start_ms"]),
        end_ms=int(segment["end_ms"]),
        confidence=None if segment.get("confidence") is None else float(segment["confidence"]),
        language=str(segment.get("language", "zh") or "zh"),
        tags=[str(tag) for tag in segment.get("tags", [])],
        # Diarized wrappers stamp a speaker cluster label; non-diarized ones omit it -> "self".
        speaker=str(segment.get("speaker", "self") or "self"),
    )
