from __future__ import annotations

import json
import select
import subprocess
from pathlib import Path

from personal_context_node.adapters.asr.command import _asr_segment
from personal_context_node.core.ports.asr import ASRResult
from personal_context_node.core.ports.errors import RetryablePortError


class PersistentCommandASRAdapter:
    """Keeps a --server ASR wrapper resident: one chunk path in, one result JSON line out,
    so the model loads once per drain instead of once per chunk."""

    def __init__(self, *, command: list[str], timeout_seconds: float = 3600.0) -> None:
        if not command:
            raise ValueError("ASR server command must not be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.model_name = "sensevoice"
        self.model_version = "funasr-sensevoice-server"
        self._proc: subprocess.Popen[str] | None = None

    def _ensure(self) -> subprocess.Popen[str]:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, start_new_session=True,
            )
        return self._proc

    def transcribe(self, audio_path: Path) -> ASRResult:
        proc = self._ensure()
        try:
            proc.stdin.write(f"{audio_path}\n")
            proc.stdin.flush()
        except BrokenPipeError as exc:
            raise RetryablePortError("ASR server stdin closed") from exc
        ready, _, _ = select.select([proc.stdout], [], [], self.timeout_seconds)
        if not ready:
            # The server is still working on this chunk; its result line would arrive later
            # and desync the one-line-in/one-line-out protocol (the NEXT chunk would read this
            # chunk's stale buffered line, silently corrupting transcripts). Kill the poisoned
            # process so the next call spawns a fresh server with an empty pipe.
            self.close()
            raise RetryablePortError(f"ASR server timed out after {self.timeout_seconds:g}s")
        line = proc.stdout.readline()
        if not line:
            self.close()
            raise RetryablePortError("ASR server exited before returning a result")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            # A partial/garbled line means the resident server crashed mid-write; reset it so
            # the pipe can't desync, and treat as retryable (a fresh server can recover).
            self.close()
            raise RetryablePortError(f"invalid ASR server JSON: {exc}") from exc
        if "error" in payload:
            raise RetryablePortError(f"ASR server error: {payload['error']}")
        self.model_name = str(payload.get("model_name", self.model_name))
        self.model_version = str(payload.get("model_version", self.model_version))
        return ASRResult(
            segments=[_asr_segment(s) for s in payload.get("segments", [])],
            backend=self.__class__.__name__,
            model_name=self.model_name,
            model_version=self.model_version,
            language=payload.get("language"),
            decode_config={"command": self.command},
            warnings=[str(w) for w in payload.get("warnings", [])],
        )

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        # The server may be blocked mid-inference, so don't wait on a graceful EOF exit —
        # kill promptly (it is stateless) and reap so close() returns fast and leaves no zombie.
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def __del__(self) -> None:  # best-effort cleanup if the drain forgets to close()
        try:
            self.close()
        except Exception:
            pass
