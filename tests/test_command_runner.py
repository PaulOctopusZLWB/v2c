from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from personal_context_node.adapters.command_runner import run_command


def test_run_command_returns_completed_process_with_output() -> None:
    completed = run_command([sys.executable, "-c", "print('hi')"], timeout_seconds=10)

    assert completed.returncode == 0
    assert completed.stdout.strip() == "hi"


def test_run_command_forwards_stdin_text() -> None:
    completed = run_command(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        stdin_text="hello",
        timeout_seconds=10,
    )

    assert completed.stdout == "HELLO"


def test_run_command_starts_child_in_new_process_group() -> None:
    # A distinct process group is what lets a timeout kill the whole subprocess tree.
    completed = run_command([sys.executable, "-c", "import os; print(os.getpgrp())"], timeout_seconds=10)

    assert completed.returncode == 0
    assert int(completed.stdout.strip()) != os.getpgrp()


def test_run_command_timeout_kills_forked_grandchild(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild.txt"
    script = tmp_path / "forker.py"
    # A wrapper that forks a grandchild (writes a marker after 1.5s) then hangs. A correct
    # process-group kill terminates the grandchild before it can write the marker.
    script.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', \"import time; time.sleep(1.5); open({str(marker)!r}, 'w').write('alive')\"])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_command([sys.executable, str(script)], timeout_seconds=0.3)

    # Wait past the grandchild's would-be write time; the marker must never appear.
    time.sleep(2.0)
    assert not marker.exists()


def test_run_command_timeout_stays_bounded_with_detached_grandchild(tmp_path: Path) -> None:
    # A grandchild that detaches into its own session escapes the group SIGKILL and keeps
    # the inherited stdout pipe open; reaping must not block on it (was a ~10s stall).
    script = tmp_path / "detacher.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'], start_new_session=True)\n"
        "time.sleep(20)\n",
        encoding="utf-8",
    )

    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_command([sys.executable, str(script)], timeout_seconds=0.5)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"timeout reaping blocked on a detached grandchild ({elapsed:.1f}s)"
