from __future__ import annotations

import json
import subprocess
from pathlib import Path

from personal_context_node.core.ports.asr import ASRSegment
from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError


class CommandASRAdapter:
    """ASR adapter for local commands or Docker wrapper scripts.

    The command is invoked as: `<command...> <audio_path>`.
    It must emit JSON to stdout:
    `{"model_name": "...", "model_version": "...", "segments": [...]}`.
    """

    def __init__(self, *, command: list[str]) -> None:
        if not command:
            raise ValueError("ASR command must not be empty")
        self.command = command
        self.model_name = "command-asr"
        self.model_version = "unknown"

    def transcribe(self, audio_path: Path) -> list[ASRSegment]:
        completed = subprocess.run(
            [*self.command, str(audio_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RetryablePortError(f"ASR command failed with exit {completed.returncode}: {completed.stderr.strip()}")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise TerminalPortError(f"invalid ASR JSON: {exc}") from exc
        self.model_name = str(payload.get("model_name", self.model_name))
        self.model_version = str(payload.get("model_version", self.model_version))
        return [ASRSegment(**segment) for segment in payload.get("segments", [])]
