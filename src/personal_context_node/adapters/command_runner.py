from __future__ import annotations

import os
import signal
import subprocess


def run_command(
    command: list[str],
    *,
    stdin_text: str | None = None,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Run an external command with a hard timeout that kills its whole process group.

    Wrapper scripts (ASR/VAD/LLM/archive) commonly fork heavyweight model workers; killing
    only the direct child — as ``subprocess.run(timeout=...)`` does — leaves those
    grandchildren holding GPU/CPU/file handles after a timeout, leaking across retries. We
    start the child in its own session/process group and, on timeout, signal the entire
    group, then re-raise ``subprocess.TimeoutExpired`` so callers keep their existing
    timeout handling.
    """
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(input=stdin_text, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_process_group(process)
        # Reap the killed process so it does not linger as a zombie; output is discarded.
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _kill_process_group(process: "subprocess.Popen[str]") -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        # No process group (already exited) or not permitted: fall back to the direct child.
        process.kill()
