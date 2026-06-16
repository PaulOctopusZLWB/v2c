from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from personal_context_node.adapters.asr.command import _asr_segment
from personal_context_node.core.ports.asr import ASRResult
from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError


class PersistentCommandASRAdapter:
    """Keeps a --server ASR wrapper resident: one chunk path in, one result JSON line out,
    so the model loads once per drain instead of once per chunk."""

    def __init__(
        self, *, command: list[str], timeout_seconds: float = 3600.0, model_version: str = "funasr-sensevoice-server"
    ) -> None:
        if not command:
            raise ValueError("ASR server command must not be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.model_name = "sensevoice"
        self.model_version = model_version
        self._proc: subprocess.Popen[str] | None = None

    def _ensure(self) -> subprocess.Popen[str]:
        if self._proc is None or self._proc.poll() is not None:
            # Discard the server's stderr: the funasr wrapper writes its (very verbose,
            # multi-MB on first-run model download) load output there, and we never read it —
            # an undrained PIPE fills the OS buffer and blocks the server before it reads a
            # chunk path from stdin, wedging the daemon. We don't need the load noise.
            self._proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, start_new_session=True,
            )
        return self._proc

    def _readline_with_timeout(self, proc: "subprocess.Popen[str]") -> str | None:
        # Bound the blocking readline so a server that stalls — including after flushing a
        # partial, newline-less line — can't hang past timeout_seconds. Returns None on
        # timeout, "" on EOF, or the line. The caller close()s on timeout, which unblocks
        # the daemon reader thread.
        holder: list[str] = []

        def _read() -> None:
            try:
                holder.append(proc.stdout.readline())
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(self.timeout_seconds)
        if reader.is_alive():
            return None
        return holder[0] if holder else ""

    def transcribe(self, audio_path: Path) -> ASRResult:
        proc = self._ensure()
        try:
            proc.stdin.write(f"{audio_path}\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise RetryablePortError("ASR server stdin closed") from exc
        line = self._readline_with_timeout(proc)
        if line is None:
            # The server is still working (or stalled mid-line); its result would later
            # desync the one-line-in/one-line-out protocol. Kill the poisoned process so the
            # next call spawns a fresh server with an empty pipe.
            self.close()
            raise RetryablePortError(f"ASR server timed out after {self.timeout_seconds:g}s")
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
            # The server marks permanently-unsupported input (e.g. a missing chunk file) as
            # terminal, matching CommandASRAdapter's exit-3 contract; everything else is transient.
            if payload.get("terminal"):
                raise TerminalPortError(f"ASR server rejected input as permanently unsupported: {payload['error']}")
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
