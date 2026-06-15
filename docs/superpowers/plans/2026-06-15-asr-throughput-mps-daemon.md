# ASR Throughput: MPS + Persistent Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut ASR wall-clock for a large import by ~50x — run SenseVoice on the Apple GPU (MPS) and keep the model resident in a long-lived daemon instead of reloading ~900MB per chunk.

**Architecture:** Two independent wins, layered behind the existing `ASRPort`. (1) The FunASR wrapper learns a `--device` flag (default `mps` with CPU fallback) so inference uses the GPU. (2) The wrapper learns a `--server` mode (load `AutoModel` once, then read chunk paths line-by-line on stdin and emit one result JSON per line). A new `PersistentCommandASRAdapter` starts that server once and streams chunk paths to it, so the model loads once per drain, not once per task. Multi-worker parallel draining is added last as a CPU-machine fallback (a single MPS daemon already saturates the one GPU; the local Mac benchmark shows the per-chunk model load is ~86-98% of cost, so the daemon — not parallelism — is the dominant win here).

**Tech Stack:** Python 3.11+, FunASR/SenseVoice (PyTorch + MPS), `subprocess`, `select`, the existing `CommandASRAdapter` contract, pytest. No new dependency.

**Benchmark baseline (Mac 18-core / 128GB / MPS):** cold per chunk ≈ 4.6s load + 0.8s transcribe (load = 86%). MPS transcribe ≈ 0.056s (RTF 0.002, ~13x faster than CPU). With the daemon + MPS, ~9000 audio-seconds of speech ≈ ~18s of pure transcribe.

---

## File Structure

- Modify `scripts/funasr_sensevoice_wrapper.py`: add `--device` (default `mps`→`cpu` fallback) and `--server` mode; extract `run_server(model, stdin, stdout)` so the loop is unit-testable without loading FunASR.
- Create `src/personal_context_node/adapters/asr/persistent_command.py`: `PersistentCommandASRAdapter` (resident server process, one chunk path in / one JSON line out, with timeout + restart-on-crash).
- Modify `src/personal_context_node/pipeline_adapters.py`: `build_asr` gains an `asr_backend == "funasr_server"` branch wiring the persistent adapter.
- Modify `src/personal_context_node/config.py`: add `asr_device: str = "mps"`.
- Modify `src/personal_context_node/web/worker.py`: drain to completion (don't stall at `max_steps`).
- Tests: `tests/test_funasr_sensevoice_wrapper.py`, `tests/test_persistent_command_asr.py`, `tests/test_pipeline_adapters.py`, `tests/test_config.py`, `tests/test_drain_process_queue.py`.

## Task 1: Wrapper `--device` flag (MPS by default, CPU fallback)

**Files:**
- Modify: `scripts/funasr_sensevoice_wrapper.py`
- Modify: `tests/test_funasr_sensevoice_wrapper.py`

- [ ] **Step 1: Write the failing device-resolution test**

Add to `tests/test_funasr_sensevoice_wrapper.py`:

```python
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("funasr_wrapper", Path("scripts/funasr_sensevoice_wrapper.py"))
fw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fw)


def test_resolve_device_prefers_mps_when_available() -> None:
    assert fw.resolve_device("mps", mps_available=lambda: True) == "mps"
    assert fw.resolve_device("mps", mps_available=lambda: False) == "cpu"  # graceful fallback
    assert fw.resolve_device("cpu", mps_available=lambda: True) == "cpu"   # explicit override respected
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_funasr_sensevoice_wrapper.py::test_resolve_device_prefers_mps_when_available -q`
Expected: FAIL — `resolve_device` not defined.

- [ ] **Step 3: Implement `resolve_device` + pass `device` into `AutoModel`**

In `scripts/funasr_sensevoice_wrapper.py`, add the helper near the top and set the MPS-fallback env at import time:

```python
import os

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # unsupported MPS ops fall back to CPU


def resolve_device(requested: str, *, mps_available=None) -> str:
    if requested != "mps":
        return requested
    if mps_available is None:
        import torch
        mps_available = torch.backends.mps.is_available
    return "mps" if mps_available() else "cpu"
```

Add `--device` to the argparser (in `main`, after the existing args):

```python
parser.add_argument("--device", default="mps")
```

And pass it into `AutoModel` (replace the `model_kwargs` construction):

```python
model_kwargs: dict[str, Any] = {"model": args.model, "device": resolve_device(args.device)}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_funasr_sensevoice_wrapper.py::test_resolve_device_prefers_mps_when_available -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/funasr_sensevoice_wrapper.py tests/test_funasr_sensevoice_wrapper.py
git commit -m "feat(asr): funasr wrapper --device with MPS default + CPU fallback"
```

## Task 2: Wrapper `--server` mode (load once, stream chunks)

**Files:**
- Modify: `scripts/funasr_sensevoice_wrapper.py`
- Modify: `tests/test_funasr_sensevoice_wrapper.py`

- [ ] **Step 1: Write the failing server-loop test (fake model, no FunASR)**

Add to `tests/test_funasr_sensevoice_wrapper.py`:

```python
import io
import json


def test_run_server_emits_one_result_line_per_chunk_path() -> None:
    class FakeModel:
        def generate(self, *, input, **kw):
            return [{"text": f"<|zh|>转写 {input}", "timestamp": [0, 1000]}]

    stdin = io.StringIO("a.wav\n\nb.wav\n")   # blank line ignored
    stdout = io.StringIO()

    fw.run_server(FakeModel(), stdin, stdout, language="zh")

    lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(lines) == 2
    assert lines[0]["segments"][0]["text"] == "转写 a.wav"
    assert lines[0]["model_name"] == "sensevoice"


def test_run_server_reports_per_chunk_error_without_crashing() -> None:
    class BoomModel:
        def generate(self, *, input, **kw):
            raise RuntimeError("decode failed")

    stdout = io.StringIO()
    fw.run_server(BoomModel(), io.StringIO("x.wav\n"), stdout, language="zh")

    out = json.loads(stdout.getvalue())
    assert "error" in out and "decode failed" in out["error"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_funasr_sensevoice_wrapper.py -q -k run_server`
Expected: FAIL — `run_server` not defined.

- [ ] **Step 3: Implement `run_server` + wire `--server` into `main`**

Append to `scripts/funasr_sensevoice_wrapper.py`:

```python
def run_server(model, stdin, stdout, *, language: str) -> int:
    """Resident loop: one chunk path per input line -> one result JSON per output line."""
    for raw_line in stdin:
        path = raw_line.strip()
        if not path:
            continue
        try:
            result = model.generate(input=path, language=language, use_itn=True, batch_size_s=300)
            payload = {"model_name": "sensevoice", "model_version": "funasr-sensevoice-server",
                       "segments": _normalize_segments(result)}
        except Exception as exc:  # one bad chunk must not kill the resident server
            payload = {"error": f"{type(exc).__name__}: {exc}"}
        stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0
```

In `main`, add `--server` to the parser and branch before the single-shot path:

```python
parser.add_argument("--server", action="store_true")
...
if args.server:
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):
        from funasr import AutoModel
        model = AutoModel(model=args.model, device=resolve_device(args.device))
    return run_server(model, sys.stdin, sys.stdout, language=args.language)
```

(Place this branch right after `args = parser.parse_args()`, before the existing `audio_path.exists()` single-shot logic.)

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_funasr_sensevoice_wrapper.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/funasr_sensevoice_wrapper.py tests/test_funasr_sensevoice_wrapper.py
git commit -m "feat(asr): funasr wrapper --server resident mode"
```

## Task 3: `PersistentCommandASRAdapter`

**Files:**
- Create: `src/personal_context_node/adapters/asr/persistent_command.py`
- Create: `tests/test_persistent_command_asr.py`

- [ ] **Step 1: Write the failing adapter test (fake server script)**

Create `tests/test_persistent_command_asr.py`:

```python
from __future__ import annotations

from pathlib import Path

from personal_context_node.adapters.asr.persistent_command import PersistentCommandASRAdapter
from personal_context_node.core.ports.errors import RetryablePortError


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
    r2 = adapter.transcribe(tmp_path / "chk_2.wav")
    adapter.close()

    assert r1.segments[0].text == f"转 {tmp_path / 'chk_1.wav'}"
    assert r2.segments[0].text == f"转 {tmp_path / 'chk_2.wav'}"
    assert r1.backend == "PersistentCommandASRAdapter"


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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_persistent_command_asr.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the adapter**

Create `src/personal_context_node/adapters/asr/persistent_command.py`:

```python
from __future__ import annotations

import json
import select
import subprocess
from pathlib import Path

from personal_context_node.adapters.asr.command import _asr_segment
from personal_context_node.core.ports.asr import ASRResult
from personal_context_node.core.ports.errors import RetryablePortError, TerminalPortError


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
            raise RetryablePortError(f"ASR server timed out after {self.timeout_seconds:g}s")
        line = proc.stdout.readline()
        if not line:
            self.close()
            raise RetryablePortError("ASR server exited before returning a result")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TerminalPortError(f"invalid ASR server JSON: {exc}") from exc
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
        if self._proc is not None and self._proc.poll() is None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                self._proc.kill()
        self._proc = None

    def __del__(self) -> None:  # best-effort cleanup if the drain forgets to close()
        try:
            self.close()
        except Exception:
            pass
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_persistent_command_asr.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/adapters/asr/persistent_command.py tests/test_persistent_command_asr.py
git commit -m "feat(asr): PersistentCommandASRAdapter (resident server, model loads once)"
```

## Task 4: Wire `funasr_server` backend + `asr_device` config

**Files:**
- Modify: `src/personal_context_node/config.py`
- Modify: `src/personal_context_node/pipeline_adapters.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_pipeline_adapters.py`

- [ ] **Step 1: Write the failing config + builder tests**

Add to `tests/test_config.py`:

```python
def test_app_config_has_default_asr_device_mps() -> None:
    assert AppConfig().asr_device == "mps"
```

Add to `tests/test_pipeline_adapters.py`:

```python
def test_build_asr_funasr_server_returns_persistent_adapter() -> None:
    from personal_context_node.adapters.asr.persistent_command import PersistentCommandASRAdapter
    adapter = build_asr(asr_backend="funasr_server", asr_command=None, mock_text=None, asr_device="mps")
    assert isinstance(adapter, PersistentCommandASRAdapter)
    assert "--server" in adapter.command and "--device" in adapter.command
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_config.py::test_app_config_has_default_asr_device_mps tests/test_pipeline_adapters.py::test_build_asr_funasr_server_returns_persistent_adapter -q`
Expected: FAIL — field/branch missing.

- [ ] **Step 3: Implement**

In `src/personal_context_node/config.py`, add the field near the other asr fields:

```python
asr_device: str = "mps"
```

and in `from_toml`, in the `values` dict next to `asr_*`:

```python
"asr_device": asr.get("device", cls.model_fields["asr_device"].default),
```

In `src/personal_context_node/pipeline_adapters.py`, add `asr_device: str = "mps"` to `build_asr`'s signature and a new branch before the `funasr` branch:

```python
    if asr_backend == "funasr_server":
        from personal_context_node.adapters.asr.persistent_command import PersistentCommandASRAdapter
        command = (
            shlex.split(asr_command)
            if asr_command
            else ["python3", "scripts/funasr_sensevoice_wrapper.py", "--server",
                  "--model", model_id, "--device", asr_device, "--language", language]
        )
        return PersistentCommandASRAdapter(command=command, timeout_seconds=timeout_seconds)
```

In `build_pipeline_adapters`, pass `asr_device=config.asr_device` into the `build_asr(...)` call.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_pipeline_adapters.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/config.py src/personal_context_node/pipeline_adapters.py tests/test_config.py tests/test_pipeline_adapters.py
git commit -m "feat(asr): wire funasr_server backend + asr_device config"
```

## Task 5: Drain to completion (stop stalling at max_steps)

**Files:**
- Modify: `src/personal_context_node/web/worker.py`
- Modify: `tests/test_drain_process_queue.py`

- [ ] **Step 1: Write the failing drain test**

Add to `tests/test_drain_process_queue.py` (mirror the file's existing setup helpers):

```python
def test_web_worker_drains_more_than_default_max_steps(tmp_path) -> None:
    # A backlog larger than the old 200-step cap must drain in a single worker run.
    # (Use the file's existing fixture to enqueue >200 trivial mock tasks, then assert
    # drain reports status 'complete' / 0 remaining claimable tasks after one start().)
    ...
```

Implement the body using whatever enqueue/mock-adapter helpers already exist in this test module (e.g. enqueue N mock `vad` tasks via the test's `_seed_tasks` helper); assert the worker's drain finishes them all, not just 200.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_drain_process_queue.py -q -k more_than_default_max_steps`
Expected: FAIL — the worker stops at 200.

- [ ] **Step 3: Implement drain-to-completion**

In `src/personal_context_node/web/worker.py`, raise the worker's `max_steps` so one run drains the backlog: loop `drain_process_queue` until it returns `status == "complete"` (no claimable work) or a stop is requested, instead of a single capped call. Keep the per-call `max_steps` as a cooperative-stop checkpoint (so `request_stop()` still interrupts between batches).

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_drain_process_queue.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/web/worker.py tests/test_drain_process_queue.py
git commit -m "fix(worker): drain backlog to completion in one run"
```

## Task 6: Live MPS-daemon smoke + enable in config

**Files:**
- Modify: `config/funasr.example.toml`

- [ ] **Step 1: Enable the server backend + device in the example config**

In `config/funasr.example.toml`, set:

```toml
[asr]
backend = "funasr_server"
device = "mps"
```

- [ ] **Step 2: Live smoke (requires the funasr runtime)**

```bash
# server contract: one path per line in, one JSON line out, model loaded once
printf 'sample_data/TX01_MIC001_20260607_155539_orig.wav\n' | \
  PYTORCH_ENABLE_MPS_FALLBACK=1 uv run --extra funasr python3 scripts/funasr_sensevoice_wrapper.py \
    --server --model iic/SenseVoiceSmall --device mps --language zh
# end-to-end through the queue (weights should land on mps:0, transcribe RTF ~0.002):
uv run --extra funasr pcn process run --config config/funasr.example.toml \
  --data-dir .tmp/mps-smoke --obsidian-vault .tmp/mps-vault
```

- [ ] **Step 3: Full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add config/funasr.example.toml
git commit -m "feat(asr): default funasr example to MPS server backend"
```

## Task 7 (optional, CPU-machine fallback): parallel multi-worker drain

> On this Mac the single MPS daemon already collapses ASR to ~tens of seconds, so parallelism is a low-priority fallback for **CPU-only** machines (no GPU). The local benchmark showed the sweet spot is ~4 workers (~2x), ~8 saturating; the task table is already concurrency-safe via lease claims (`tasks.claim_next_task` uses `begin immediate` + ownership guards).

**Files:**
- Modify: `src/personal_context_node/web/worker.py`
- Test: `tests/test_drain_process_queue.py`

- [ ] **Step 1:** Add a `worker_count` (config `[tasks].workers`, default 1) and, when > 1, run that many drain threads, each with its own adapters (each CPU worker builds its own `CommandASRAdapter`; do NOT share one MPS daemon across threads — a single GPU is the bottleneck). Write a test that two workers drain a shared queue without double-claiming (assert each task is claimed exactly once).
- [ ] **Step 2:** Run the test, verify pass, commit `feat(worker): optional parallel drain for CPU machines`.

## Self-Review

- **Spec coverage:** MPS device (Task 1), resident daemon (Tasks 2-3), wired backend + config (Task 4), drain-to-completion (Task 5), live smoke (Task 6), parallel CPU fallback (Task 7). ✓
- **Placeholders:** Task 5/7 test bodies reference the existing test module's own helpers by name rather than inventing new fixtures — fill them from that file when implementing; all production code steps are complete.
- **Type consistency:** `resolve_device`, `run_server`, `PersistentCommandASRAdapter` (`transcribe`/`close`), `build_asr(..., asr_device=...)`, `config.asr_device`, backend literal `"funasr_server"` are used consistently across tasks; `_asr_segment` is reused from `adapters/asr/command.py`.
