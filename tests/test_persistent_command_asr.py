from __future__ import annotations

import time
from pathlib import Path

from personal_context_node.adapters.asr.persistent_command import PersistentCommandASRAdapter
from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError


def _server_script(tmp_path: Path) -> Path:
    # A fake resident ASR server: reads chunk paths, echoes one result line per path,
    # and counts that the "model load" line runs only ONCE.
    script = tmp_path / "fake_server.py"
    script.write_text(
        "import json, sys\n"
        "sys.stderr.write('LOADED\\n')\n"  # one-time load marker
        "for line in sys.stdin:\n"
        "    p = line.strip()\n"
        "    if not p: continue\n"
        "    print(json.dumps({'model_name':'sensevoice','model_version':'v','segments':"
        "[{'text':'转 '+p,'start_ms':0,'end_ms':1000,'language':'zh'}]}), flush=True)\n",
        encoding="utf-8",
    )
    return script


def test_persistent_adapter_reuses_one_process_across_chunks(tmp_path: Path) -> None:
    adapter = PersistentCommandASRAdapter(command=["python3", str(_server_script(tmp_path))], timeout_seconds=10)

    r1 = adapter.transcribe(tmp_path / "chk_1.wav")
    # Anchor the headline "model loads once" guarantee: capture the resident process after the
    # first chunk, then assert the SAME Popen object and OS pid serve the second chunk. Without
    # this, a regression that respawns per chunk (reloading the ~900MB model every time) would
    # still echo correct text and slip through — the whole ~50x win silently lost.
    proc_after_first = adapter._proc
    pid_after_first = proc_after_first.pid
    r2 = adapter.transcribe(tmp_path / "chk_2.wav")
    assert adapter._proc is proc_after_first  # not respawned: same process object
    assert adapter._proc.pid == pid_after_first  # same OS process -> model loaded exactly once
    adapter.close()

    assert r1.segments[0].text == f"转 {tmp_path / 'chk_1.wav'}"
    assert r2.segments[0].text == f"转 {tmp_path / 'chk_2.wav'}"
    assert r1.backend == "PersistentCommandASRAdapter"


def test_persistent_adapter_raises_terminal_for_terminal_flagged_error(tmp_path: Path) -> None:
    # A server that flags an error line as terminal (e.g. a missing chunk file, mirroring the
    # one-shot exit-code-3 contract) must surface as TerminalPortError so the task fails fast
    # instead of burning the whole retry budget. A non-flagged error stays retryable (covered
    # by the server-dies test above).
    script = tmp_path / "terminal_err.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    if not line.strip(): continue\n"
        "    print(json.dumps({'error': 'audio file does not exist', 'terminal': True}), flush=True)\n",
        encoding="utf-8",
    )
    adapter = PersistentCommandASRAdapter(command=["python3", str(script)], timeout_seconds=10)
    try:
        adapter.transcribe(tmp_path / "missing.wav")
    except TerminalPortError as exc:
        assert "permanently unsupported" in str(exc)
    except RetryablePortError:
        raise AssertionError("terminal-flagged server error must raise TerminalPortError, not RetryablePortError")
    else:
        raise AssertionError("expected TerminalPortError for a terminal-flagged server error")
    finally:
        adapter.close()


def test_persistent_adapter_carries_speaker_labels_from_diarized_server(tmp_path: Path) -> None:
    # The paraformer diarize wrapper emits segments with a "speaker" cluster label; the resident
    # adapter must surface it on ASRSegment (so transcription can write speaker_cluster_id).
    script = tmp_path / "diar_server.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    p = line.strip()\n"
        "    if not p: continue\n"
        "    print(json.dumps({'model_name':'paraformer-diarize','model_version':'v','segments':["
        "{'text':'你好','start_ms':0,'end_ms':1000,'speaker':'spk_01','language':'zh'},"
        "{'text':'在','start_ms':1000,'end_ms':1500,'speaker':'spk_02','language':'zh'}]}), flush=True)\n",
        encoding="utf-8",
    )
    adapter = PersistentCommandASRAdapter(command=["python3", str(script)], timeout_seconds=10)
    result = adapter.transcribe(tmp_path / "file.wav")
    adapter.close()

    assert [s.speaker for s in result.segments] == ["spk_01", "spk_02"]


def test_persistent_adapter_raises_retryable_when_server_dies(tmp_path: Path) -> None:
    script = tmp_path / "dies.py"
    script.write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    adapter = PersistentCommandASRAdapter(command=["python3", str(script)], timeout_seconds=5)
    try:
        adapter.transcribe(tmp_path / "chk.wav")
    except RetryablePortError as exc:
        assert "ASR server" in str(exc)
    else:
        raise AssertionError("expected RetryablePortError when the server exits")


def test_persistent_adapter_resets_process_on_timeout_to_prevent_desync(tmp_path: Path) -> None:
    # A server that never replies in time: the adapter must kill the in-flight process on
    # timeout so its late result line can never be read by the NEXT chunk (off-by-one desync).
    script = tmp_path / "hang.py"
    script.write_text("import sys, time\nfor line in sys.stdin:\n    time.sleep(30)\n", encoding="utf-8")
    adapter = PersistentCommandASRAdapter(command=["python3", str(script)], timeout_seconds=0.2)
    proc = adapter._ensure()

    try:
        adapter.transcribe(tmp_path / "chk_1.wav")
    except RetryablePortError:
        pass
    else:
        raise AssertionError("expected a timeout RetryablePortError")

    assert adapter._proc is None  # poisoned process dropped; next transcribe spawns fresh
    assert proc.poll() is not None  # the in-flight server was actually terminated


def test_persistent_adapter_tolerates_verbose_server_stderr(tmp_path: Path) -> None:
    # The funasr server floods stderr during model load; the adapter must not deadlock on an
    # undrained stderr pipe (it discards stderr), so a noisy server still returns results.
    script = tmp_path / "noisy.py"
    script.write_text(
        "import sys, json\n"
        "sys.stderr.write('x' * 300000)\n"  # flood stderr BEFORE reading any chunk path
        "sys.stderr.flush()\n"
        "for line in sys.stdin:\n"
        "    p = line.strip()\n"
        "    if not p: continue\n"
        "    print(json.dumps({'model_name':'x','model_version':'v','segments':"
        "[{'text':'转 '+p,'start_ms':0,'end_ms':1,'language':'zh'}]}), flush=True)\n",
        encoding="utf-8",
    )
    adapter = PersistentCommandASRAdapter(command=["python3", str(script)], timeout_seconds=10)

    result = adapter.transcribe(tmp_path / "chk.wav")
    adapter.close()

    assert result.segments[0].text == f"转 {tmp_path / 'chk.wav'}"


def test_persistent_adapter_times_out_on_partial_line_stall(tmp_path: Path) -> None:
    # A server that flushes a partial (newline-less) line then stalls must still be bounded
    # by timeout_seconds — the blocking readline can't hang on the missing newline.
    script = tmp_path / "partial.py"
    script.write_text(
        "import sys, time\n"
        "for line in sys.stdin:\n"
        "    sys.stdout.write('{\"model_name\"')\n"  # partial JSON, no newline
        "    sys.stdout.flush()\n"
        "    time.sleep(30)\n",
        encoding="utf-8",
    )
    adapter = PersistentCommandASRAdapter(command=["python3", str(script)], timeout_seconds=0.3)

    start = time.monotonic()
    try:
        adapter.transcribe(tmp_path / "chk.wav")
    except RetryablePortError:
        pass
    else:
        raise AssertionError("expected a timeout on the partial-line stall")
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"readline hung on a newline-less line ({elapsed:.1f}s)"
    assert adapter._proc is None
