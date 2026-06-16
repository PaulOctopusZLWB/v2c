from __future__ import annotations

import json
import subprocess
import threading


class PersistentCommandEmbedAdapter:
    """Keeps a --server CAM++ embed wrapper resident: one audio path in, one voiceprint JSON line
    out, so the model loads once per extraction run instead of once per segment.

    Mirrors PersistentCommandASRAdapter's subprocess lifecycle: lazy-spawn on first embed, a
    bounded readline so a stalled server can't hang past timeout_seconds, and a prompt kill in
    close() (the server is stateless). The wire protocol is the embed wrapper's: an input line
    ``{"segment_id", "audio_path"}`` produces an output line ``{"segment_id", "embedding": [...]}``
    or ``{"segment_id", "error": ...}``.
    """

    def __init__(self, *, command: list[str], timeout_seconds: float = 3600.0) -> None:
        if not command:
            raise ValueError("embed server command must not be empty")
        self.command = command
        self.timeout_seconds = timeout_seconds
        self._proc: subprocess.Popen[str] | None = None

    def _ensure(self) -> subprocess.Popen[str]:
        if self._proc is None or self._proc.poll() is not None:
            # Discard the server's stderr: the funasr wrapper writes its (very verbose, multi-MB on
            # first-run model download) load output there, and we never read it -- an undrained PIPE
            # fills the OS buffer and blocks the server before it reads a line from stdin.
            self._proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, start_new_session=True,
            )
        return self._proc

    def _readline_with_timeout(self, proc: "subprocess.Popen[str]") -> str | None:
        # Bound the blocking readline so a server that stalls -- including after flushing a partial,
        # newline-less line -- can't hang past timeout_seconds. Returns None on timeout, "" on EOF,
        # or the line. The caller close()s on timeout, which unblocks the daemon reader thread.
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

    def embed(self, audio_path: str) -> list[float]:
        proc = self._ensure()
        try:
            proc.stdin.write(json.dumps({"segment_id": "_", "audio_path": audio_path}) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise RuntimeError("embed server stdin closed") from exc
        line = self._readline_with_timeout(proc)
        if line is None:
            # The server is still working (or stalled mid-line); its result would later desync the
            # one-line-in/one-line-out protocol. Kill the poisoned process so the next call spawns a
            # fresh server with an empty pipe.
            self.close()
            raise RuntimeError(f"embed server timed out after {self.timeout_seconds:g}s")
        if not line:
            self.close()
            raise RuntimeError("embed server exited before returning a result")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            self.close()
            raise RuntimeError(f"invalid embed server JSON: {exc}") from exc
        if "error" in payload:
            raise RuntimeError(f"embed server error: {payload['error']}")
        return [float(v) for v in payload["embedding"]]

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
        # The server may be blocked mid-inference, so don't wait on a graceful EOF exit -- kill
        # promptly (it is stateless) and reap so close() returns fast and leaves no zombie.
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def __del__(self) -> None:  # best-effort cleanup if the caller forgets to close()
        try:
            self.close()
        except Exception:
            pass
