# Local Web Control Panel Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> This v2 supersedes `2026-06-13-local-web-control-panel.md`. It exists because the v1 plan introduced a **second orchestration system** (`pipeline_runs` + `run_control.py`) alongside the existing task queue, **hard-wired an LLM acceptance gate into core queries** (breaking autonomous launchd operation), and **duplicated the speaker-attribution write path**. v2 removes all three by treating the web layer as a thin observer and one additional worker over the *existing* `tasks` queue.

**Goal:** Build a localhost-only web control panel that observes and drives the existing Personal Context Node pipeline (Device → Import → ASR → Transcript Review → LLM → Publish) without adding a parallel orchestrator, without changing default autonomous behavior, and without creating a second source of truth for any piece of state.

**Architecture:** The web layer is purely additive. It (1) **enqueues** work into the existing `tasks` queue via `ingest.import_audio_files` / `tasks.enqueue_task` / `tasks.retry_task`; (2) **drives** the queue by running the *same* `process_once` drain loop the CLI uses, inside one in-process worker thread whose cooperative stop is an in-process `threading.Event`; (3) **observes** progress by reading the persisted `tasks` and `job_runs` tables (so it sees launchd-driven runs too); (4) adds one additive table `transcript_segment_reviews` and an **opt-in** acceptance gate that defaults off so autonomous runs are unchanged; (5) reuses a single extracted speaker-mapping writer for both the Obsidian-markdown sync path and the web API. SQLite is switched to WAL so the web process and launchd can share the database safely.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, SQLite (WAL), existing service functions, React, Vite, TypeScript, TanStack Query, native EventSource/SSE, pytest, httpx/TestClient, Vitest, Playwright.

---

## Confirmed Scope

Local-only control panel, not a public hosted app.

- Host binding: `127.0.0.1` only. Default port `8765`.
- No login, no cloud hosting in v1.
- No real-time microphone subtitle stream.
- No raw audio upload off the local machine.
- Obsidian remains the final knowledge surface.
- **No rewrite of the pipeline core. No second orchestrator.**

Primary workflow:

```text
Device -> Import -> ASR Prep -> Transcript Review -> LLM -> Publish / Review
```

### Design rules carried from the architecture review

These are non-negotiable invariants for v2. Every task is checked against them.

1. **One orchestrator.** The `tasks` queue + `process_once` is the only place that decides "what runs next." The web layer never invents a parallel run/state machine. Run identity and history come from the existing `job_runs` table; live task state comes from `tasks`.
2. **One worker model, many safe workers.** The web process runs the *same* `process_once` drain loop the CLI runs. Lease-based claiming (`claim_next_task` + `reclaim_expired_tasks`) already makes concurrent workers safe, so the web worker and launchd can coexist without double-processing.
3. **Cooperative stop on the real loop.** Stop is an in-process `threading.Event` checked *between* `process_once` calls (one work unit each). No `stop_requested` column, because nothing durable needs to consume it — the worker lives in the web process.
4. **The acceptance gate is opt-in and lives in exactly one predicate.** Default `require_accepted_transcripts = False` keeps autonomous launchd behavior identical. The gate SQL exists only in `transcript_review.accepted_segments_clause`; `session_summaries` and `llm_processing` apply it only when the flag is on.
5. **One source of truth per piece of state.** Speaker→person mapping has a single writer function (extracted from `speaker_review.py`) used by both the markdown sync path and the web API. Memory-candidate confirm/reject stays on the existing Obsidian-markdown control surface in v1 (web is read-only preview); we do **not** add a second candidate-mutation path.
6. **SQL lives in domain modules.** Web routes call typed service functions only. Any new query lives in a domain module (`transcript_review.py`, `transcription.py`, `tasks.py`), never inline in `web/routes_*.py`.

---

## Existing Codebase Anchors

Verified against the current tree before writing this plan:

- `src/personal_context_node/process_runner.py:62` — `process_once(*, config, run_id, vad, asr, llm=None, max_chunk_ms=30000)`; the declarative `PIPELINE` DAG and `PROCESS_TASK_ORDER` live here. `preview_next_process_task` at `:124`.
- `src/personal_context_node/tasks.py` — `enqueue_task` (`:46`), `claim_next_task` (`:91`), `retry_task` (`:211`), `reclaim_expired_tasks` (`:171`), `process_status_rows` (`:342`, returns `task_id, task_type, target_type, target_id, status, attempt_count, last_error, duration_ms, model_name, model_version`).
- `src/personal_context_node/jobs.py:23` — `record_job_run(*, config, job_name, run_id, operation)`; `job_status_rows` also exported.
- `src/personal_context_node/ingest.py:73` — `import_audio_files(*, config, source_dir) -> IngestImportResult(imported_files=int)`; importing **auto-enqueues a `vad` task per file** (`ingest.py:147`).
- `src/personal_context_node/cli.py` — `_run_all` (`:219`) is the reference drain loop; `_build_vad`/`_build_asr`/`_build_llm` (`:1535`/`:1561`/`:1596`) build adapters from config but raise `typer.BadParameter`. `_load_config` (`:932`).
- `src/personal_context_node/session_summaries.py:24` — `summarize_session(*, config, session_id, llm)`; transcript query at `:28-37` (`from transcript_segments where session_id = ? and is_active = 1`).
- `src/personal_context_node/llm_processing.py:24` — `generate_daily_context(*, config, day, llm)`; LLM-input transcript query at `:31-37` (alias `ts`, `where s.date_key = ? and ts.is_active = 1`). The metrics query at `:223-228` is **not** LLM input and must not be gated.
- `src/personal_context_node/speaker_review.py:188-216` — existing upsert into `speaker_mappings` and `segment_person_overrides` (the writer we extract); `_upsert_person` at `:401`.
- `src/personal_context_node/obsidian_publish.py:22` — `publish_obsidian_day(*, config, day, source_run_id=None)`.
- `src/personal_context_node/storage/sqlite.py:381` — `connect(path)` sets `row_factory` and `pragma foreign_keys = on` (no WAL yet); `initialize` (`:389`), `fetch_all` (`:543`), `_ensure_column` helper used by migrations.
- `src/personal_context_node/config.py:29` — `AppConfig(BaseModel)`; `from_toml` (`:75`); adapter/config fields: `vad_backend`, `vad_command`, `vad_threshold`, `merge_gap_ms`, `min_speech_ms`, `vad_model_id`, `vad_model_revision`, `asr_backend`, `asr_command`, `asr_language`, `asr_model_name`, `asr_model_id`, `asr_model_version`, `llm_backend`, `llm_command`, `max_chunk_ms`, `send_speaker_labels`, `owner_did`.

---

## File Structure Plan

### Backend (new)

- `src/personal_context_node/pipeline_adapters.py` — pure-domain VAD/ASR/LLM adapter builders (raise `ValueError`, no Typer), plus `build_pipeline_adapters(config)`. Shared by CLI and web worker.
- `src/personal_context_node/transcript_review.py` — review-status persistence, session acceptance computation, and the single `accepted_segments_clause` gate predicate.
- `src/personal_context_node/llm_results.py` — read-only domain helpers: parse the persisted `summaries` rows (session + daily) and list `memory_candidates` for display. No writes.
- `src/personal_context_node/web/__init__.py` — package marker.
- `src/personal_context_node/web/config.py` — resolve `AppConfig` for the server (delegates to existing loader).
- `src/personal_context_node/web/app.py` — FastAPI app factory, router registration, worker lifecycle, static mount.
- `src/personal_context_node/web/server.py` — `pcn web` entry point; binds `127.0.0.1`.
- `src/personal_context_node/web/worker.py` — `PipelineWorker`: single drain-loop thread + cooperative stop.
- `src/personal_context_node/web/routes_status.py` — health, overview, tasks, runs (read-only, from persisted state).
- `src/personal_context_node/web/routes_pipeline.py` — import, run, stop, retry, SSE.
- `src/personal_context_node/web/routes_transcripts.py` — transcript listing + acceptance actions.
- `src/personal_context_node/web/routes_speakers.py` — persons list/create + speaker→person assignment + segment override.
- `src/personal_context_node/web/routes_audio.py` — segment audio playback.
- `src/personal_context_node/web/routes_llm.py` — read-only session summary + daily context/candidates.

### Backend (modified)

- `src/personal_context_node/storage/sqlite.py` — WAL + `busy_timeout` in `connect`; add `transcript_segment_reviews` table + migration.
- `src/personal_context_node/process_runner.py` — add `drain_process_queue(...)` (the shared loop) + `DrainResult`.
- `src/personal_context_node/cli.py` — `_run_all` delegates to `drain_process_queue`; `_build_*` delegate to `pipeline_adapters`; add `pcn web` command.
- `src/personal_context_node/config.py` — add `require_accepted_transcripts: bool = False` and wire `from_toml`.
- `src/personal_context_node/session_summaries.py` — apply gate predicate only when flag on.
- `src/personal_context_node/llm_processing.py` — apply gate predicate to the LLM-input query only when flag on.
- `src/personal_context_node/speaker_review.py` — extract `upsert_speaker_mapping` / `upsert_segment_person_override` writers used by both sync and web.
- `src/personal_context_node/transcription.py` — add `segment_audio_path(*, config, segment_id)` domain helper.

### Frontend (new)

Foundation + state: `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/index.html`, `web/src/main.tsx`, `web/src/App.tsx` (live container, not static), `web/src/styles.css`, `web/src/api/{client,types,events}.ts`, `web/src/hooks/usePipelineStatus.ts` (SSE + TanStack Query).

Components: `web/src/components/{PipelineRail,RunInspector}.tsx`, `web/src/features/transcript/{TranscriptReviewPanel,SegmentRow}.tsx`, `web/src/features/speakers/SpeakerPanel.tsx`, `web/src/features/llm/LlmResultPanel.tsx`.

### Tests

Backend: `tests/test_sqlite_wal.py`, `test_pipeline_adapters.py`, `test_drain_process_queue.py`, `test_web_status_api.py`, `test_web_pipeline_api.py`, `test_transcript_review.py`, `test_llm_acceptance_gate.py`, `test_web_transcript_api.py`, `test_web_speaker_api.py`, `test_web_audio_api.py`, `test_llm_results.py`, `test_web_llm_api.py`, `test_web_e2e.py`.

Frontend: `web/src/__tests__/{PipelineRail,RunInspector,TranscriptReviewPanel,SpeakerPanel,LlmResultPanel,App}.test.tsx`, `web/e2e/control-panel.spec.ts`.

---

## Data Model

### `transcript_segment_reviews` (only new table)

Additive. Records human acceptance status before LLM consumption. A missing row means `pending_review`.

```sql
create table if not exists transcript_segment_reviews (
  segment_id text primary key references transcript_segments(segment_id),
  status text not null,
  reviewer text not null default 'local_user',
  note text,
  reviewed_at text not null,
  updated_at text not null
);

create index if not exists idx_segment_reviews_status
on transcript_segment_reviews(status, reviewed_at);
```

Valid `status`: `pending_review`, `accepted`, `rejected`, `needs_fix`. Only explicit `accepted` rows are eligible for LLM (and only when `require_accepted_transcripts` is on).

**No `pipeline_runs` table.** Run state = existing `tasks` (live) + `job_runs` (history). Stop = in-process event.

---

## API Contract

All endpoints localhost-only under `/api`. Endpoints are listed here only if a task in this plan implements them — no aspirational routes.

```http
GET  /api/health
GET  /api/status/overview        # worker running + task status counts
GET  /api/status/tasks           # tasks.process_status_rows
GET  /api/status/runs            # jobs.job_status_rows
GET  /api/events                 # SSE: status.snapshot on change

POST /api/pipeline/import        # {source_dir, wait?} -> import + enqueue; returns immediately. wait=true also drains.
POST /api/pipeline/run           # start the background worker to drain whatever is queued
POST /api/pipeline/stop          # cooperative stop (worker finishes current unit)
POST /api/pipeline/tasks/{task_id}/retry

GET  /api/transcripts/days                                # days that have sessions (+ session_count)
GET  /api/transcripts/days/{day}/sessions                 # sessions for a day (+ review_status) — navigation
GET  /api/transcripts/sessions/{session_id}
POST /api/transcripts/segments/{segment_id}/review        # {status, note}
POST /api/transcripts/sessions/{session_id}/accept-remaining

GET  /api/persons                                         # list persons (for the picker)
POST /api/persons                                         # {display_name, person_type?} -> create person
POST /api/speakers/{speaker}/assign-person                # {person_id}
POST /api/transcripts/segments/{segment_id}/person-override  # {person_id}

GET  /api/audio/segments/{segment_id}    # audio/wav

GET  /api/llm/sessions/{session_id}/summary   # read-only: persisted session summary, or 404
GET  /api/llm/days/{day}                       # read-only: daily context + memory candidates (display only)
```

**Read-only by design (v1):** the `GET /api/llm/...` endpoints expose what the pipeline already persisted in `summaries` / `memory_candidates` for *review*. They never mutate. Memory-candidate confirm/reject/edit is **not** an HTTP action in v1 — that final sign-off stays on the Obsidian-markdown control surface (`confirm_checked_candidates`) to avoid a competing source of truth.

**Deferred to a later phase (explicit non-goals for v1):** memory-candidate confirm/reject/edit over HTTP, publish-trigger endpoint (publish is the `obsidian_publish` task already enqueued by the DAG; the web "run" drains it), and per-day LLM "generate" endpoints (the DAG generates daily context after sessions summarize).

---

## Implementation Tasks

### Task 1: SQLite WAL For Two-Process Safety

The web server and launchd both touch the same SQLite file. The default rollback journal will throw `database is locked`. Enable WAL + a busy timeout in the one place every connection is opened.

**Files:**
- Modify: `src/personal_context_node/storage/sqlite.py:381`
- Test: `tests/test_sqlite_wal.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_sqlite_wal.py`:

```python
from __future__ import annotations

from pathlib import Path

from personal_context_node.storage.sqlite import connect, initialize


def test_connect_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "test.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        initialize(conn)
        journal_mode = conn.execute("pragma journal_mode").fetchone()[0]
        busy_timeout = conn.execute("pragma busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert journal_mode.lower() == "wal"
    assert busy_timeout >= 5000
```

- [ ] **Step 2: Run test and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_sqlite_wal.py -q
```

Expected: `assert 'delete' == 'wal'` (or similar non-WAL journal mode).

- [ ] **Step 3: Set pragmas in `connect`**

In `src/personal_context_node/storage/sqlite.py`, the current `connect` is:

```python
def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    return conn
```

Add WAL + busy timeout right after `foreign_keys`:

```python
def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma journal_mode = wal")
    conn.execute("pragma busy_timeout = 5000")
    return conn
```

- [ ] **Step 4: Run test and the full storage suite**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_sqlite_wal.py tests/test_sqlite_migrations.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/storage/sqlite.py tests/test_sqlite_wal.py
git commit -m "feat: enable sqlite wal for multi-process access"
```

---

### Task 2: Extract Shared Adapter Builders And Drain Loop

The web worker must run the *exact* execution path the CLI runs. Extract the adapter builders (currently Typer-coupled in `cli.py`) into a pure-domain module, and extract the queue-drain loop (currently inline in `_run_all`) into `process_runner`. Both CLI and web then share one implementation. This is the prerequisite that lets the web layer drive the pipeline without duplicating it.

**Files:**
- Create: `src/personal_context_node/pipeline_adapters.py`
- Modify: `src/personal_context_node/process_runner.py`
- Modify: `src/personal_context_node/cli.py:219` and `:1535`-`:1610`
- Test: `tests/test_pipeline_adapters.py`, `tests/test_drain_process_queue.py`

- [ ] **Step 1: Write failing adapter-builder test**

Create `tests/test_pipeline_adapters.py`:

```python
from __future__ import annotations

import pytest

from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.config import AppConfig
from personal_context_node.pipeline_adapters import build_asr, build_llm, build_pipeline_adapters, build_vad


def test_build_vad_energy_returns_energy_adapter() -> None:
    assert isinstance(build_vad(vad_backend="energy", vad_command=None, vad_threshold=0.5), EnergyVadAdapter)


def test_build_asr_mock_returns_mock_adapter() -> None:
    assert isinstance(build_asr(asr_backend="mock", asr_command=None, mock_text=None), MockASRAdapter)


def test_build_unknown_backend_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_llm(llm_backend="nope", llm_command=None)


def test_build_pipeline_adapters_uses_config_defaults() -> None:
    config = AppConfig()
    adapters = build_pipeline_adapters(config=config)
    assert isinstance(adapters.llm, RuleBasedLLMAdapter)
```

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_pipeline_adapters.py -q
```

Expected: `ModuleNotFoundError: No module named 'personal_context_node.pipeline_adapters'`.

- [ ] **Step 3: Create the pure-domain builders**

Create `src/personal_context_node/pipeline_adapters.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from personal_context_node.adapters.asr.command import CommandASRAdapter
from personal_context_node.adapters.asr.mock import MockASRAdapter
from personal_context_node.adapters.llm.command import CommandLLMAdapter
from personal_context_node.adapters.llm.mock import MockLLMAdapter
from personal_context_node.adapters.llm.rule_based import RuleBasedLLMAdapter
from personal_context_node.adapters.vad.command import CommandVADAdapter
from personal_context_node.adapters.vad.energy import EnergyVadAdapter
from personal_context_node.adapters.vad.mock import MockVADAdapter
from personal_context_node.config import AppConfig
from personal_context_node.core.ports.asr import ASRPort
from personal_context_node.core.ports.llm import LLMPort
from personal_context_node.core.ports.vad import VADPort


@dataclass(frozen=True)
class PipelineAdapters:
    vad: VADPort
    asr: ASRPort
    llm: LLMPort


def build_vad(
    *,
    vad_backend: str,
    vad_command: str | None,
    vad_threshold: float,
    merge_gap_ms: int = 250,
    min_speech_ms: int = 300,
    model_id: str = "fsmn-vad",
    model_revision: str | None = None,
) -> VADPort:
    if vad_backend == "energy":
        return EnergyVadAdapter(threshold=vad_threshold, merge_gap_ms=merge_gap_ms, min_speech_ms=min_speech_ms)
    if vad_backend == "mock":
        return MockVADAdapter()
    if vad_backend == "command":
        if not vad_command:
            raise ValueError("vad_command is required when vad_backend is 'command'")
        return CommandVADAdapter(command=vad_command.split())
    if vad_backend == "funasr":
        command = vad_command.split() if vad_command else ["python3", "scripts/funasr_vad_wrapper.py", "--model", model_id]
        if not vad_command and model_revision is not None:
            command.extend(["--model-revision", model_revision])
        return CommandVADAdapter(command=command)
    raise ValueError("vad_backend must be 'energy', 'mock', 'command', or 'funasr'")


def build_asr(
    *,
    asr_backend: str,
    asr_command: str | None,
    mock_text: str | None,
    language: str = "zh",
    model_name: str = "mock-asr",
    model_id: str = "iic/SenseVoiceSmall",
    model_version: str = "funasr-sensevoice-local",
) -> ASRPort:
    if asr_backend == "mock":
        return MockASRAdapter(text=mock_text, language=language, model_name=model_name)
    if asr_backend == "command":
        if not asr_command:
            raise ValueError("asr_command is required when asr_backend is 'command'")
        return CommandASRAdapter(command=asr_command.split())
    if asr_backend == "funasr":
        command = (
            asr_command.split()
            if asr_command
            else [
                "python3",
                "scripts/funasr_sensevoice_wrapper.py",
                "--model",
                model_id,
                "--model-version",
                model_version,
                "--language",
                language,
            ]
        )
        return CommandASRAdapter(command=command)
    raise ValueError("asr_backend must be 'mock', 'command', or 'funasr'")


def build_llm(*, llm_backend: str, llm_command: str | None) -> LLMPort:
    if llm_backend == "rule_based":
        return RuleBasedLLMAdapter()
    if llm_backend == "mock":
        return MockLLMAdapter()
    if llm_backend == "command":
        if not llm_command:
            raise ValueError("llm_command is required when llm_backend is 'command'")
        return CommandLLMAdapter(command=llm_command.split())
    raise ValueError("llm_backend must be 'rule_based', 'mock', or 'command'")


def build_pipeline_adapters(*, config: AppConfig) -> PipelineAdapters:
    return PipelineAdapters(
        vad=build_vad(
            vad_backend=config.vad_backend,
            vad_command=config.vad_command,
            vad_threshold=config.vad_threshold,
            merge_gap_ms=config.merge_gap_ms,
            min_speech_ms=config.min_speech_ms,
            model_id=config.vad_model_id,
            model_revision=config.vad_model_revision,
        ),
        asr=build_asr(
            asr_backend=config.asr_backend,
            asr_command=config.asr_command,
            mock_text=None,
            language=config.asr_language,
            model_name=config.asr_model_name,
            model_id=config.asr_model_id,
            model_version=config.asr_model_version,
        ),
        llm=build_llm(llm_backend=config.llm_backend, llm_command=config.llm_command),
    )
```

> If any imported adapter class name or `config` field differs from the anchors above, correct the import/field to match the real symbol — do not invent new ones.

- [ ] **Step 4: Make CLI delegate to the domain builders**

In `src/personal_context_node/cli.py`, replace the bodies of `_build_vad`, `_build_asr`, `_build_llm` (`:1535`-`:1610`) so they call the new functions and translate `ValueError` to Typer:

```python
from personal_context_node.pipeline_adapters import build_asr as _domain_build_asr
from personal_context_node.pipeline_adapters import build_llm as _domain_build_llm
from personal_context_node.pipeline_adapters import build_vad as _domain_build_vad


def _build_vad(*, vad_backend, vad_command, vad_threshold, merge_gap_ms=250, min_speech_ms=300, model_id="fsmn-vad", model_revision=None):
    try:
        return _domain_build_vad(
            vad_backend=vad_backend, vad_command=vad_command, vad_threshold=vad_threshold,
            merge_gap_ms=merge_gap_ms, min_speech_ms=min_speech_ms, model_id=model_id, model_revision=model_revision,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _build_asr(*, asr_backend, asr_command, mock_text, language="zh", model_name="mock-asr", model_id="iic/SenseVoiceSmall", model_version="funasr-sensevoice-local"):
    try:
        return _domain_build_asr(
            asr_backend=asr_backend, asr_command=asr_command, mock_text=mock_text,
            language=language, model_name=model_name, model_id=model_id, model_version=model_version,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _build_llm(*, llm_backend, llm_command):
    try:
        return _domain_build_llm(llm_backend=llm_backend, llm_command=llm_command)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
```

Remove the now-unused direct adapter imports in `cli.py` only if a lint failure flags them; otherwise leave them.

- [ ] **Step 5: Write failing drain-loop test**

Create `tests/test_drain_process_queue.py`:

```python
from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.process_runner import drain_process_queue


def test_drain_empty_queue_reports_complete(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    result = drain_process_queue(config=config, vad=_Unused(), asr=_Unused(), llm=_Unused())
    assert result.status == "complete"
    assert result.process_steps == 0


def test_drain_stops_when_should_stop_true(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    result = drain_process_queue(config=config, vad=_Unused(), asr=_Unused(), llm=_Unused(), should_stop=lambda: True)
    assert result.status == "stopped"
    assert result.process_steps == 0


class _Unused:
    pass
```

- [ ] **Step 6: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_drain_process_queue.py -q
```

Expected: `ImportError: cannot import name 'drain_process_queue'`.

- [ ] **Step 7: Add `drain_process_queue` to `process_runner.py`**

Add to `src/personal_context_node/process_runner.py` (imports `record_job_run`, `uuid4`, and `Callable` are needed; `process_once` and `preview_next_process_task` are already in the module):

```python
from typing import Callable
from uuid import uuid4

from personal_context_node.jobs import record_job_run


@dataclass(frozen=True)
class DrainResult:
    process_steps: int
    tasks_succeeded: int
    status: str  # "complete" | "stopped" | "step_limit"


def drain_process_queue(
    *,
    config: AppConfig,
    vad: VADPort,
    asr: ASRPort,
    llm: LLMPort | None = None,
    max_chunk_ms: int | None = None,
    max_steps: int = 200,
    should_stop: Callable[[], bool] = lambda: False,
    job_name: str = "process.drain",
) -> DrainResult:
    chunk_ms = max_chunk_ms if max_chunk_ms is not None else config.max_chunk_ms
    process_steps = 0
    tasks_succeeded = 0
    status = "step_limit"
    while process_steps < max_steps:
        if should_stop():
            status = "stopped"
            break
        run_id = f"run_{uuid4().hex}"
        result = record_job_run(
            config=config,
            job_name=job_name,
            run_id=run_id,
            operation=lambda run_id=run_id: process_once(
                config=config, run_id=run_id, vad=vad, asr=asr, llm=llm, max_chunk_ms=chunk_ms
            ),
        ).result
        if result.status == "no_task":
            status = "complete"
            break
        process_steps += 1
        if result.status == "succeeded":
            tasks_succeeded += 1
    if status == "step_limit" and preview_next_process_task(config=config).status == "no_task":
        status = "complete"
    return DrainResult(process_steps=process_steps, tasks_succeeded=tasks_succeeded, status=status)
```

- [ ] **Step 8: Make `_run_all` delegate to `drain_process_queue`**

In `src/personal_context_node/cli.py:219`, replace the inline `while process_steps < max_steps:` loop (everything from `process_steps = 0` through the loop end, the lines that build `run_id`/call `record_job_run`/`process_once`) with a single call, preserving the existing job name and echo:

```python
from personal_context_node.process_runner import drain_process_queue

    drain = drain_process_queue(
        config=config, vad=vad, asr=asr, llm=llm,
        max_chunk_ms=max_chunk_ms, max_steps=max_steps,
        job_name="run-all.process",
    )
    status = "complete" if drain.status == "complete" else drain.status
    typer.echo(
        " ".join(
            [
                f"imported_files={import_result.imported_files}",
                f"process_steps={drain.process_steps}",
                f"tasks_succeeded={drain.tasks_succeeded}",
                f"status={status}",
            ]
        )
    )
    if status != "complete":
        raise typer.Exit(code=1)
```

- [ ] **Step 9: Run focused + regression tests**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_pipeline_adapters.py tests/test_drain_process_queue.py tests/test_run_all_cli.py -q
```

Expected: all pass. If `tests/test_run_all_cli.py` asserts on the `run-all.process` job name, it still holds because we passed `job_name="run-all.process"`.

- [ ] **Step 10: Commit**

```bash
git add src/personal_context_node/pipeline_adapters.py src/personal_context_node/process_runner.py src/personal_context_node/cli.py tests/test_pipeline_adapters.py tests/test_drain_process_queue.py
git commit -m "refactor: share pipeline adapter builders and drain loop"
```

---

### Task 3: Web Entry Point And Health

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/personal_context_node/cli.py`
- Create: `src/personal_context_node/web/__init__.py`, `config.py`, `app.py`, `server.py`
- Test: `tests/test_web_status_api.py`

- [ ] **Step 1: Write failing health test**

Create `tests/test_web_status_api.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.web.app import create_app


def test_web_health_returns_local_runtime_metadata(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "host": "127.0.0.1",
        "data_dir": str(config.data_dir),
        "obsidian_vault": str(config.obsidian_vault),
        "require_accepted_transcripts": False,
    }
```

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_status_api.py -q
```

Expected: `ModuleNotFoundError: No module named 'personal_context_node.web'`.

- [ ] **Step 3: Add dependencies**

In `pyproject.toml` add to `dependencies`: `"fastapi>=0.115.0"`, `"uvicorn>=0.30.0"`. In `[dependency-groups].dev` add `"httpx>=0.27.0"` (TestClient needs it).

- [ ] **Step 4: Create the package + app factory**

Create `src/personal_context_node/web/__init__.py`:

```python
from __future__ import annotations
```

Create `src/personal_context_node/web/config.py`:

```python
from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig


def load_web_config(*, config_path: Path | None, data_dir: Path | None, obsidian_vault: Path | None) -> AppConfig:
    if config_path is not None:
        return AppConfig.from_toml(config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    overrides: dict[str, object] = {}
    if data_dir is not None:
        overrides["data_dir"] = data_dir
    if obsidian_vault is not None:
        overrides["obsidian_vault"] = obsidian_vault
    return AppConfig(**overrides)
```

Create `src/personal_context_node/web/app.py`:

```python
from __future__ import annotations

from fastapi import FastAPI

from personal_context_node.config import AppConfig


def create_app(*, config: AppConfig) -> FastAPI:
    app = FastAPI(title="Personal Context Node Control Panel")
    app.state.config = config

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "host": "127.0.0.1",
            "data_dir": str(config.data_dir),
            "obsidian_vault": str(config.obsidian_vault),
            "require_accepted_transcripts": bool(config.require_accepted_transcripts),
        }

    return app
```

> `config.require_accepted_transcripts` is added in Task 6. Until then, this attribute does not exist. Implement Task 6 before running the health test, **or** temporarily read it via `getattr(config, "require_accepted_transcripts", False)`. Use the `getattr` form now and tighten it in Task 6.

Apply the `getattr` form in `app.py` for now:

```python
            "require_accepted_transcripts": bool(getattr(config, "require_accepted_transcripts", False)),
```

Create `src/personal_context_node/web/server.py`:

```python
from __future__ import annotations

from pathlib import Path

import uvicorn

from personal_context_node.web.app import create_app
from personal_context_node.web.config import load_web_config


def run_web_server(
    *,
    config_path: Path | None,
    data_dir: Path | None,
    obsidian_vault: Path | None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    if host != "127.0.0.1":
        raise ValueError("web server v1 must bind to 127.0.0.1")
    config = load_web_config(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault)
    uvicorn.run(create_app(config=config), host=host, port=port)
```

- [ ] **Step 5: Add `pcn web` command**

In `src/personal_context_node/cli.py` add near the other top-level commands:

```python
from personal_context_node.web.server import run_web_server


@app.command(name="web")
def web_cmd(
    config_path: Path | None = typer.Option(None, "--config", help="Path to config/local.toml."),
    data_dir: Path | None = typer.Option(None, help="Local data directory."),
    obsidian_vault: Path | None = typer.Option(None, help="Dedicated PersonalContext Obsidian vault path."),
    host: str = typer.Option("127.0.0.1", help="Bind host. v1 only allows 127.0.0.1."),
    port: int = typer.Option(8765, min=1, max=65535, help="Bind port."),
) -> None:
    try:
        run_web_server(config_path=config_path, data_dir=data_dir, obsidian_vault=obsidian_vault, host=host, port=port)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
```

- [ ] **Step 6: Run focused test**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_status_api.py -q
```

Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/personal_context_node/cli.py src/personal_context_node/web tests/test_web_status_api.py
git commit -m "feat: add local web server entrypoint"
```

---

### Task 4: Read-Only Status API Over The Real Queue

The panel observes the existing queue. No new state — just project `tasks` and `job_runs`.

**Files:**
- Create: `src/personal_context_node/web/routes_status.py`
- Modify: `src/personal_context_node/web/app.py`
- Test: extend `tests/test_web_status_api.py`

- [ ] **Step 1: Write failing status test**

Append to `tests/test_web_status_api.py`:

```python
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.tasks import enqueue_task


def test_status_tasks_lists_enqueued_task(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
    finally:
        conn.close()
    enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="aud_x")
    client = TestClient(create_app(config=config))

    response = client.get("/api/status/tasks")

    assert response.status_code == 200
    rows = response.json()["tasks"]
    assert any(row["task_type"] == "vad" and row["status"] == "pending" for row in rows)


def test_status_overview_reports_counts_and_worker_idle(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    response = client.get("/api/status/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["worker_running"] is False
    assert "status_counts" in payload
```

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_status_api.py -q
```

Expected: 404 on `/api/status/tasks`.

- [ ] **Step 3: Add status routes**

Create `src/personal_context_node/web/routes_status.py`:

```python
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Request

from personal_context_node.config import AppConfig
from personal_context_node.jobs import job_status_rows
from personal_context_node.tasks import process_status_rows


router = APIRouter(prefix="/api/status")


@router.get("/tasks")
def status_tasks(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"tasks": process_status_rows(config=config)}


@router.get("/runs")
def status_runs(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"runs": job_status_rows(config=config)}


@router.get("/overview")
def status_overview(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    rows = process_status_rows(config=config)
    counts = Counter(str(row["status"]) for row in rows)
    worker = getattr(request.app.state, "worker", None)
    return {
        "worker_running": bool(worker.is_running()) if worker is not None else False,
        "status_counts": dict(counts),
        "total_tasks": len(rows),
    }
```

> Confirm `jobs.job_status_rows` accepts `config=` as a keyword (it is imported in `cli.py:30`). If its real signature differs, match it.

In `src/personal_context_node/web/app.py`, register the router inside `create_app` before `return app`:

```python
from personal_context_node.web.routes_status import router as status_router

    app.include_router(status_router)
```

- [ ] **Step 4: Run focused test**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_status_api.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/web/routes_status.py src/personal_context_node/web/app.py tests/test_web_status_api.py
git commit -m "feat: expose read-only pipeline status api"
```

---

### Task 5: In-Process Worker, Cooperative Stop, And Pipeline Commands

One worker thread runs `drain_process_queue` with an in-process stop event. Import/run start it; stop sets the event; the worker stops claiming after the current work unit. SSE streams status snapshots from the persisted `tasks` table, so the panel reflects any worker (web or launchd).

**Files:**
- Create: `src/personal_context_node/web/worker.py`
- Create: `src/personal_context_node/web/routes_pipeline.py`
- Modify: `src/personal_context_node/web/app.py`
- Test: `tests/test_web_pipeline_api.py`

- [ ] **Step 1: Write failing worker + endpoint tests**

Create `tests/test_web_pipeline_api.py`:

```python
from __future__ import annotations

import math
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.web.app import create_app


def test_import_enqueues_vad_task_and_does_not_create_parallel_run_table(tmp_path: Path) -> None:
    source = tmp_path / "NO NAME"
    _write_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_backend="mock", vad_backend="mock", llm_backend="mock")
    client = TestClient(create_app(config=config))

    response = client.post("/api/pipeline/import", json={"source_dir": str(source)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported_files"] == 1
    assert payload["queued"] is True  # default import enqueues and returns immediately, no drain
    conn = connect(config.database_path)
    try:
        initialize(conn)
        tables = {row["name"] for row in fetch_all(conn, "select name from sqlite_master where type='table'")}
        vad_tasks = fetch_all(conn, "select task_id from tasks where task_type = 'vad'")
    finally:
        conn.close()
    assert "pipeline_runs" not in tables  # no parallel orchestrator
    assert len(vad_tasks) == 1


def test_import_with_wait_runs_pipeline_through_mock_backends(tmp_path: Path) -> None:
    source = tmp_path / "NO NAME"
    _write_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", asr_backend="mock", vad_backend="mock", llm_backend="mock")
    client = TestClient(create_app(config=config))

    response = client.post("/api/pipeline/import", json={"source_dir": str(source), "wait": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["imported_files"] == 1
    assert payload["drain"]["status"] in {"complete", "step_limit"}
    assert payload["drain"]["tasks_succeeded"] >= 1


def test_stop_is_idempotent_when_worker_idle(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    response = client.post("/api/pipeline/stop")

    assert response.status_code == 200
    assert response.json()["stop_requested"] is True


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        frames = bytearray()
        for index in range(16000):
            sample = int(10000 * math.sin(2 * math.pi * 440 * index / 16000))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))
```

> **Default import is enqueue-only and returns immediately** so a long real ASR run never blocks the HTTP request — the UI then calls `POST /api/pipeline/run` to start the background worker. `wait=true` forces a synchronous drain and is used only by tests (and any explicit "import and wait" affordance), which keeps these tests deterministic without a sleep-based assertion. The background-thread `start()`/`is_running()` path is exercised by `/api/pipeline/run` and the overview test.

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_pipeline_api.py -q
```

Expected: 404 on `/api/pipeline/import`.

- [ ] **Step 3: Implement the worker**

Create `src/personal_context_node/web/worker.py`:

```python
from __future__ import annotations

import threading

from personal_context_node.config import AppConfig
from personal_context_node.pipeline_adapters import build_pipeline_adapters
from personal_context_node.process_runner import DrainResult, drain_process_queue


class PipelineWorker:
    """Single drain-loop worker. Cooperative stop via an in-process Event.

    Lease-based task claiming (claim_next_task + reclaim_expired_tasks) makes this
    safe to run alongside a launchd worker on the same database.
    """

    def __init__(self, *, config: AppConfig) -> None:
        self._config = config
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_result: DrainResult | None = None

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def drain_now(self, *, max_steps: int = 200) -> DrainResult:
        """Synchronous drain (used in request handlers and tests)."""
        self._stop.clear()
        adapters = build_pipeline_adapters(config=self._config)
        result = drain_process_queue(
            config=self._config, vad=adapters.vad, asr=adapters.asr, llm=adapters.llm,
            max_steps=max_steps, should_stop=self._stop.is_set, job_name="web.drain",
        )
        self._last_result = result
        return result

    def start(self, *, max_steps: int = 200) -> bool:
        """Start the background drain thread if not already running. Returns started?"""
        with self._lock:
            if self.is_running():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, kwargs={"max_steps": max_steps}, daemon=True)
            self._thread.start()
            return True

    def request_stop(self) -> None:
        self._stop.set()

    def _run(self, *, max_steps: int) -> None:
        adapters = build_pipeline_adapters(config=self._config)
        self._last_result = drain_process_queue(
            config=self._config, vad=adapters.vad, asr=adapters.asr, llm=adapters.llm,
            max_steps=max_steps, should_stop=self._stop.is_set, job_name="web.drain",
        )
```

- [ ] **Step 4: Add pipeline routes**

Create `src/personal_context_node/web/routes_pipeline.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.ingest import import_audio_files
from personal_context_node.tasks import process_status_rows, retry_task


router = APIRouter(prefix="/api/pipeline")
# SSE lives at /api/events per the API contract, NOT under /api/pipeline — so it gets its own router.
events_router = APIRouter(prefix="/api")


class ImportRequest(BaseModel):
    source_dir: str
    wait: bool = False  # default: import + enqueue, return immediately; the UI then calls /run


@router.post("/import")
def import_stage(request: Request, payload: ImportRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    result = import_audio_files(config=config, source_dir=Path(payload.source_dir))
    # Default path returns immediately so a long ASR run never blocks the request.
    response: dict[str, object] = {"imported_files": result.imported_files, "queued": True}
    if payload.wait:  # synchronous drain — tests / explicit "import and wait" only
        drain = request.app.state.worker.drain_now()
        response["drain"] = {
            "status": drain.status,
            "process_steps": drain.process_steps,
            "tasks_succeeded": drain.tasks_succeeded,
        }
    return response


@router.post("/run")
def run_stage(request: Request) -> dict[str, object]:
    started = request.app.state.worker.start()
    return {"worker_started": started, "worker_running": request.app.state.worker.is_running()}


@router.post("/stop")
def stop_stage(request: Request) -> dict[str, object]:
    request.app.state.worker.request_stop()
    return {"stop_requested": True, "worker_running": request.app.state.worker.is_running()}


@router.post("/tasks/{task_id}/retry")
def retry_task_route(request: Request, task_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    try:
        result = retry_task(config=config, task_id=task_id)
    except ValueError as exc:  # tasks.retry_task raises ValueError("task not found: ...")
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # RetryTaskResult is exactly (task_id, status) — see tasks.py:29-32.
    return {"task_id": result.task_id, "status": result.status}
```

> `tasks.retry_task` (`tasks.py:211-234`) raises `ValueError` on a missing task and otherwise returns `RetryTaskResult(task_id, status="pending")` — there is no `updated`/`retried` field, so the route maps the `ValueError` to 404 and echoes `task_id`/`status` directly.

- [ ] **Step 5: Add SSE endpoint at `/api/events` (status snapshots from persisted state)**

Append to `src/personal_context_node/web/routes_pipeline.py`. The route is on `events_router` so its full path is `/api/events`, matching the contract and the frontend's `EventSource("/api/events")`:

```python
@events_router.get("/events")
async def events_stream(request: Request) -> StreamingResponse:
    config: AppConfig = request.app.state.config

    async def stream():
        last_signature: str | None = None
        # Emit an immediate snapshot, then poll for changes.
        for _ in range(10_000):
            if await request.is_disconnected():
                break
            rows = process_status_rows(config=config)
            signature = json.dumps([[r["task_id"], r["status"]] for r in rows], sort_keys=True)
            if signature != last_signature:
                last_signature = signature
                payload = {"tasks": rows, "worker_running": request.app.state.worker.is_running()}
                yield "event: status.snapshot\n"
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream")
```

In `src/personal_context_node/web/app.py`, construct the worker and register **both** routers inside `create_app`:

```python
from personal_context_node.web.routes_pipeline import events_router, router as pipeline_router
from personal_context_node.web.worker import PipelineWorker

    app.state.worker = PipelineWorker(config=config)
    app.include_router(pipeline_router)
    app.include_router(events_router)  # serves GET /api/events
```

Add a path-contract test to `tests/test_web_pipeline_api.py` so the SSE endpoint can never silently drift back under `/api/pipeline`:

```python
def test_events_endpoint_is_served_at_api_events(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))

    with client.stream("GET", "/api/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        first = next(response.iter_lines())
        assert first == "event: status.snapshot"

    # The wrong path must NOT exist.
    assert client.get("/api/pipeline/events").status_code == 404
```

- [ ] **Step 6: Run focused tests**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_pipeline_api.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/personal_context_node/web/worker.py src/personal_context_node/web/routes_pipeline.py src/personal_context_node/web/app.py tests/test_web_pipeline_api.py
git commit -m "feat: drive the existing task queue from one web worker"
```

---

### Task 6: Transcript Acceptance Data Model And Config Flag

Additive table + the single gate predicate + the opt-in config flag (default off).

**Files:**
- Modify: `src/personal_context_node/storage/sqlite.py`
- Modify: `src/personal_context_node/config.py`
- Create: `src/personal_context_node/transcript_review.py`
- Test: `tests/test_transcript_review.py`

- [ ] **Step 1: Write failing review tests**

Create `tests/test_transcript_review.py`:

```python
from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.transcript_review import (
    accept_remaining_segments,
    accepted_segments_clause,
    review_segment,
    reviewed_segments_for_session,
    session_review_status,
)


def test_config_defaults_gate_off() -> None:
    assert AppConfig().require_accepted_transcripts is False


def test_accepted_segments_clause_is_a_correlated_exists() -> None:
    clause = accepted_segments_clause("ts")
    assert "transcript_segment_reviews" in clause
    assert "ts.segment_id" in clause
    assert "accepted" in clause


def test_review_segment_persists_status(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)

    review_segment(config=config, segment_id="seg_1", status="accepted", note="")

    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select segment_id, status, reviewer, note from transcript_segment_reviews")
    finally:
        conn.close()
    assert rows == [{"segment_id": "seg_1", "status": "accepted", "reviewer": "local_user", "note": ""}]


def test_session_review_status_blocks_on_needs_fix(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    review_segment(config=config, segment_id="seg_1", status="accepted", note="")
    review_segment(config=config, segment_id="seg_2", status="needs_fix", note="听不清")
    assert session_review_status(config=config, session_id="ses_test") == "blocked"


def test_accept_remaining_accepts_only_pending(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session_with_segments(config.database_path)
    review_segment(config=config, segment_id="seg_1", status="rejected", note="噪音")
    assert accept_remaining_segments(config=config, session_id="ses_test") == {"accepted": 1}
    rows = reviewed_segments_for_session(config=config, session_id="ses_test")
    assert [(r["segment_id"], r["review_status"]) for r in rows] == [("seg_1", "rejected"), ("seg_2", "accepted")]


def _insert_session_with_segments(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 2000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:02+08:00", "derived_from_segments", 2, 2000, "seg_1", "2087-05-10T08:00:03+08:00", "2087-05-10T08:00:03+08:00"),
        )
        for index, segment_id in enumerate(["seg_1", "seg_2"]):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_test", f"chk_{segment_id}", "ses_test", index * 1000, (index + 1) * 1000, f"text {index + 1}", "zh", "self", "self", f"ev_{index + 1}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_transcript_review.py -q
```

Expected: `AttributeError: ... require_accepted_transcripts` then `ModuleNotFoundError: ... transcript_review`.

- [ ] **Step 3: Add config flag**

In `src/personal_context_node/config.py`, add to `AppConfig` (near `send_speaker_labels`, `:60`):

```python
    require_accepted_transcripts: bool = False
```

In `from_toml` (`:75`), where the `llm` section is read, add:

```python
            "require_accepted_transcripts": llm.get(
                "require_accepted_transcripts",
                cls.model_fields["require_accepted_transcripts"].default,
            ),
```

- [ ] **Step 4: Add the schema + migration**

In `src/personal_context_node/storage/sqlite.py` `SCHEMA`, add the `transcript_segment_reviews` table (DDL from the Data Model section). In the migration block of `initialize` (where `_ensure_column` calls live), add:

```python
    conn.execute(
        """
        create table if not exists transcript_segment_reviews (
          segment_id text primary key references transcript_segments(segment_id),
          status text not null,
          reviewer text not null default 'local_user',
          note text,
          reviewed_at text not null,
          updated_at text not null
        )
        """
    )
    conn.execute("create index if not exists idx_segment_reviews_status on transcript_segment_reviews(status, reviewed_at)")
```

- [ ] **Step 5: Create `transcript_review.py`**

Create `src/personal_context_node/transcript_review.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


VALID_REVIEW_STATUSES = {"pending_review", "accepted", "rejected", "needs_fix"}


def accepted_segments_clause(alias: str = "ts") -> str:
    """The single source of the LLM acceptance gate predicate.

    Callers paste this into a WHERE clause (with a leading 'and') only when
    config.require_accepted_transcripts is True.
    """
    return (
        f"and exists (select 1 from transcript_segment_reviews review "
        f"where review.segment_id = {alias}.segment_id and review.status = 'accepted')"
    )


def review_segment(*, config: AppConfig, segment_id: str, status: str, note: str = "", reviewer: str = "local_user") -> None:
    if status not in VALID_REVIEW_STATUSES - {"pending_review"}:
        raise ValueError(f"invalid transcript review status: {status}")
    now = _now()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            """
            insert into transcript_segment_reviews (segment_id, status, reviewer, note, reviewed_at, updated_at)
            values (?, ?, ?, ?, ?, ?)
            on conflict(segment_id) do update set
              status = excluded.status, reviewer = excluded.reviewer, note = excluded.note,
              reviewed_at = excluded.reviewed_at, updated_at = excluded.updated_at
            """,
            (segment_id, status, reviewer, note, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def reviewed_segments_for_session(*, config: AppConfig, session_id: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select ts.segment_id, ts.text, ts.speaker, ts.start_ms, ts.end_ms,
                   coalesce(r.status, 'pending_review') as review_status, r.note
            from transcript_segments ts
            left join transcript_segment_reviews r on r.segment_id = ts.segment_id
            where ts.session_id = ? and ts.is_active = 1
            order by ts.start_ms, ts.segment_id
            """,
            (session_id,),
        )
    finally:
        conn.close()


def session_review_status(*, config: AppConfig, session_id: str) -> str:
    rows = reviewed_segments_for_session(config=config, session_id=session_id)
    statuses = {str(r["review_status"]) for r in rows}
    if not rows or "needs_fix" in statuses:
        return "blocked"
    if "pending_review" in statuses:
        return "pending_review"
    return "accepted"


def accept_remaining_segments(*, config: AppConfig, session_id: str) -> dict[str, int]:
    rows = reviewed_segments_for_session(config=config, session_id=session_id)
    accepted = 0
    for row in rows:
        if row["review_status"] == "pending_review":
            review_segment(config=config, segment_id=str(row["segment_id"]), status="accepted", note="")
            accepted += 1
    return {"accepted": accepted}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 6: Tighten the health endpoint**

Now that the flag exists, change `app.py` health back from `getattr(...)` to direct access:

```python
            "require_accepted_transcripts": bool(config.require_accepted_transcripts),
```

- [ ] **Step 7: Run focused tests**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_transcript_review.py tests/test_web_status_api.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/personal_context_node/storage/sqlite.py src/personal_context_node/config.py src/personal_context_node/transcript_review.py src/personal_context_node/web/app.py tests/test_transcript_review.py
git commit -m "feat: add transcript acceptance model and opt-in gate flag"
```

---

### Task 7: Opt-In LLM Acceptance Gate (Autonomous Default Preserved)

Apply the gate predicate in `session_summaries` and `llm_processing` **only when `config.require_accepted_transcripts` is True**. With the default `False`, existing autonomous launchd runs are byte-for-byte unchanged. This is the task that the v1 plan got wrong by hard-wiring the gate.

**Files:**
- Modify: `src/personal_context_node/session_summaries.py:28-37`
- Modify: `src/personal_context_node/llm_processing.py:31-37`
- Test: `tests/test_llm_acceptance_gate.py`

- [ ] **Step 1: Write failing gate tests (both modes)**

Create `tests/test_llm_acceptance_gate.py`:

```python
from __future__ import annotations

from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.core.ports.llm import DailyContext, SessionSummary
from personal_context_node.llm_processing import generate_daily_context
from personal_context_node.session_summaries import summarize_session
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.transcript_review import review_segment


class RecordingLLM:
    def __init__(self) -> None:
        self.session_segments: list[dict[str, object]] = []
        self.daily_segments: list[dict[str, object]] = []

    def generate_session_summary(self, *, session_id: str, transcript_segments):
        self.session_segments = transcript_segments
        return SessionSummary(session_id=session_id, headline="h", summary="s", topics=[], decisions=[], todos=[], open_questions=[])

    def generate_daily_context(self, *, day: str, transcript_segments):
        self.daily_segments = transcript_segments
        return DailyContext(day=day, summary="s", todos=[], facts=[], inferences=[], memory_candidates=[])


def test_gate_off_default_sends_all_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")  # gate off
    _insert_session(config.database_path)
    llm = RecordingLLM()
    summarize_session(config=config, session_id="ses_test", llm=llm)
    assert {s["segment_id"] for s in llm.session_segments} == {"seg_accepted", "seg_other"}


def test_gate_on_session_summary_only_accepted(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", require_accepted_transcripts=True)
    _insert_session(config.database_path)
    review_segment(config=config, segment_id="seg_accepted", status="accepted", note="")
    review_segment(config=config, segment_id="seg_other", status="rejected", note="")
    llm = RecordingLLM()
    summarize_session(config=config, session_id="ses_test", llm=llm)
    assert [s["segment_id"] for s in llm.session_segments] == ["seg_accepted"]


def test_gate_on_daily_only_accepted(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", require_accepted_transcripts=True)
    _insert_session(config.database_path)
    review_segment(config=config, segment_id="seg_accepted", status="accepted", note="")
    llm = RecordingLLM()
    generate_daily_context(config=config, day="2087-05-10", llm=llm)
    assert [s["segment_id"] for s in llm.daily_segments] == ["seg_accepted"]


def _insert_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 2000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:02+08:00", "derived_from_segments", 2, 2000, "seg_accepted", "2087-05-10T08:00:03+08:00", "2087-05-10T08:00:03+08:00"),
        )
        for index, segment_id in enumerate(["seg_accepted", "seg_other"]):
            conn.execute(
                "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (segment_id, "aud_test", f"chk_{segment_id}", "ses_test", index * 1000, (index + 1) * 1000, segment_id, "zh", "self", "self", f"ev_{index}", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:04+08:00"),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_llm_acceptance_gate.py -q
```

Expected: the two `gate_on` tests fail (`['seg_accepted', 'seg_other'] != ['seg_accepted']`); `gate_off` passes.

- [ ] **Step 3: Gate the session-summary query**

In `src/personal_context_node/session_summaries.py`, change the query build in `summarize_session` (`:28-37`) to conditionally append the clause:

```python
from personal_context_node.transcript_review import accepted_segments_clause

        gate = accepted_segments_clause("transcript_segments") if config.require_accepted_transcripts else ""
        segments = fetch_all(
            conn,
            f"""
            select segment_id, speaker, start_ms, end_ms, text, evidence_id
            from transcript_segments
            where session_id = ? and is_active = 1
              {gate}
            order by start_ms, segment_id
            """,
            (session_id,),
        )
```

- [ ] **Step 4: Gate the daily LLM-input query only**

In `src/personal_context_node/llm_processing.py`, change the LLM-input query in `generate_daily_context` (`:31-37`, alias `ts`):

```python
from personal_context_node.transcript_review import accepted_segments_clause

        gate = accepted_segments_clause("ts") if config.require_accepted_transcripts else ""
        segments = fetch_all(
            conn,
            f"""
            select ts.segment_id, ts.session_id, ts.speaker, ts.start_ms, ts.end_ms, ts.text, ts.evidence_id
            from transcript_segments ts
            join sessions s on s.session_id = ts.session_id
            where s.date_key = ? and ts.is_active = 1
              {gate}
            order by s.started_at, ts.start_ms
            """,
            (day,),
        )
```

> Do **not** touch the metrics query at `llm_processing.py:223-228` — it computes stats, not LLM input, and must remain ungated. Match the exact `select`/`join` text already in the file; only add the `{gate}` line.

- [ ] **Step 5: Run gate + regression tests**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_llm_acceptance_gate.py tests/test_session_summaries.py tests/test_llm_processing.py -q
```

Expected: all pass. Existing `test_session_summaries.py` / `test_llm_processing.py` fixtures do **not** set acceptance, but because the gate defaults off they are unaffected — confirming the autonomous path is preserved.

- [ ] **Step 6: Commit**

```bash
git add src/personal_context_node/session_summaries.py src/personal_context_node/llm_processing.py tests/test_llm_acceptance_gate.py
git commit -m "feat: opt-in accepted-transcript gate for llm input"
```

---

### Task 8: Transcript Review API (Including Day → Session Navigation)

Thin routes over `transcript_review.py`. Includes the by-day → session list the frontend uses to navigate to a session (the chosen navigation model).

**Files:**
- Modify: `src/personal_context_node/transcript_review.py` (add `list_days`, `sessions_for_day`)
- Create: `src/personal_context_node/web/routes_transcripts.py`
- Modify: `src/personal_context_node/web/app.py`
- Test: `tests/test_web_transcript_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_web_transcript_api.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


def test_session_transcript_returns_pending_segments(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.get("/api/transcripts/sessions/ses_test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "ses_test"
    assert payload["review_status"] == "pending_review"
    assert payload["segments"][0]["review_status"] == "pending_review"


def test_review_segment_endpoint_accepts(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/transcripts/segments/seg_1/review", json={"status": "accepted", "note": ""})

    assert response.status_code == 200
    assert response.json() == {"segment_id": "seg_1", "status": "accepted"}


def test_review_segment_rejects_invalid_status(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/transcripts/segments/seg_1/review", json={"status": "bogus"})

    assert response.status_code == 400


def test_days_and_sessions_for_day_navigation(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_session(config.database_path)
    client = TestClient(create_app(config=config))

    days = client.get("/api/transcripts/days").json()["days"]
    assert [d["day"] for d in days] == ["2087-05-10"]
    assert days[0]["session_count"] == 1

    sessions = client.get("/api/transcripts/days/2087-05-10/sessions").json()["sessions"]
    assert sessions[0]["session_id"] == "ses_test"
    assert sessions[0]["review_status"] == "pending_review"


def _insert_session(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 1000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:01+08:00", "derived_from_segments", 1, 1000, "seg_1", "2087-05-10T08:00:02+08:00", "2087-05-10T08:00:02+08:00"),
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("seg_1", "aud_test", "chk_1", "ses_test", 0, 1000, "你好", "zh", "self", "self", "ev_1", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:02+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_transcript_api.py -q
```

Expected: 404.

- [ ] **Step 3: Add day/session listing to `transcript_review.py`**

Append to `src/personal_context_node/transcript_review.py`:

```python
def list_days(*, config: AppConfig) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select date_key as day, count(*) as session_count
            from sessions
            group by date_key
            order by date_key desc
            """,
        )
    finally:
        conn.close()


def sessions_for_day(*, config: AppConfig, day: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        sessions = fetch_all(
            conn,
            "select session_id, started_at, segment_count from sessions where date_key = ? order by started_at",
            (day,),
        )
    finally:
        conn.close()
    # review_status is computed per session via the existing helper (N+1 is fine for a local single-user panel).
    for session in sessions:
        session["review_status"] = session_review_status(config=config, session_id=str(session["session_id"]))
    return sessions
```

- [ ] **Step 4: Add transcript routes**

Create `src/personal_context_node/web/routes_transcripts.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.transcript_review import (
    accept_remaining_segments,
    list_days,
    review_segment,
    reviewed_segments_for_session,
    session_review_status,
    sessions_for_day,
)


router = APIRouter(prefix="/api/transcripts")


class ReviewSegmentRequest(BaseModel):
    status: str
    note: str = ""


@router.get("/days")
def transcript_days(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"days": list_days(config=config)}


@router.get("/days/{day}/sessions")
def transcript_day_sessions(request: Request, day: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {"day": day, "sessions": sessions_for_day(config=config, day=day)}


@router.get("/sessions/{session_id}")
def session_transcript(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {
        "session_id": session_id,
        "review_status": session_review_status(config=config, session_id=session_id),
        "segments": reviewed_segments_for_session(config=config, session_id=session_id),
    }


@router.post("/segments/{segment_id}/review")
def review_segment_route(request: Request, segment_id: str, payload: ReviewSegmentRequest) -> dict[str, str]:
    config: AppConfig = request.app.state.config
    try:
        review_segment(config=config, segment_id=segment_id, status=payload.status, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"segment_id": segment_id, "status": payload.status}


@router.post("/sessions/{session_id}/accept-remaining")
def accept_remaining_route(request: Request, session_id: str) -> dict[str, int]:
    config: AppConfig = request.app.state.config
    return accept_remaining_segments(config=config, session_id=session_id)
```

In `src/personal_context_node/web/app.py` register inside `create_app`:

```python
from personal_context_node.web.routes_transcripts import router as transcripts_router

    app.include_router(transcripts_router)
```

- [ ] **Step 5: Run focused tests**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_transcript_api.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/personal_context_node/transcript_review.py src/personal_context_node/web/routes_transcripts.py src/personal_context_node/web/app.py tests/test_web_transcript_api.py
git commit -m "feat: expose transcript acceptance and day/session navigation api"
```

---

### Task 9: Speaker/Person — Single Writer Shared With Markdown Sync

Extract the existing `speaker_mappings` / `segment_person_overrides` upsert from `speaker_review.py:188-216` into reusable writer functions, then have both `sync_speaker_review` and the web API call them. No duplicate SQL, one source of truth.

**Files:**
- Modify: `src/personal_context_node/speaker_review.py:188-216`
- Create: `src/personal_context_node/web/routes_speakers.py`
- Modify: `src/personal_context_node/web/app.py`
- Test: `tests/test_web_speaker_api.py` (and rely on existing `tests/test_speaker_review.py` for regression)

- [ ] **Step 1: Write failing speaker API tests**

Create `tests/test_web_speaker_api.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize
from personal_context_node.web.app import create_app


def test_assign_speaker_to_person(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/speakers/spk_1/assign-person", json={"person_id": "per_paul"})

    assert response.status_code == 200
    assert response.json() == {"speaker": "spk_1", "person_id": "per_paul", "person_label": "Paul"}
    conn = connect(config.database_path)
    try:
        rows = fetch_all(conn, "select speaker, person_id, person_label from speaker_mappings")
    finally:
        conn.close()
    assert rows == [{"speaker": "spk_1", "person_id": "per_paul", "person_label": "Paul"}]


def test_assign_unknown_person_returns_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/speakers/spk_1/assign-person", json={"person_id": "ghost"})

    assert response.status_code == 404


def test_segment_person_override(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    response = client.post("/api/transcripts/segments/seg_1/person-override", json={"person_id": "per_paul"})

    assert response.status_code == 200
    assert response.json() == {"segment_id": "seg_1", "person_id": "per_paul", "person_label": "Paul"}


def _insert_person_and_segment(database_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("per_paul", "Paul", "self", 1, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 1000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into sessions (session_id, date_key, started_at, ended_at, source, segment_count, active_speech_ms, first_segment_id, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("ses_test", "2087-05-10", "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:01+08:00", "derived_from_segments", 1, 1000, "seg_1", "2087-05-10T08:00:02+08:00", "2087-05-10T08:00:02+08:00"),
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("seg_1", "aud_test", "chk_1", "ses_test", 0, 1000, "你好", "zh", "spk_1", "spk_1", "ev_1", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:02+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_speaker_api.py -q
```

Expected: 404.

- [ ] **Step 3: Extract the shared writers in `speaker_review.py`**

Read `speaker_review.py:180-230` first. The existing loop upserts `speaker_mappings` (with columns `speaker, speaker_mapping_id, person_label, speaker_cluster_id, person_id, confidence, source, created_at, updated_at`) and `segment_person_overrides` (`segment_id, person_label, updated_at, person_id`), calling `_upsert_person`. Extract two module-level functions that take an open connection (so `sync_speaker_review` keeps its single transaction) and the existing source tag:

```python
def upsert_speaker_mapping(conn, *, speaker: str, person_id: str, person_label: str, now: str, source: str = "speaker_review") -> None:
    conn.execute(
        """
        insert into speaker_mappings (
          speaker, speaker_mapping_id, person_label, speaker_cluster_id, person_id, confidence, source, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(speaker) do update set
          person_label = excluded.person_label,
          speaker_cluster_id = excluded.speaker_cluster_id,
          person_id = excluded.person_id,
          confidence = excluded.confidence,
          source = excluded.source,
          updated_at = excluded.updated_at
        """,
        (speaker, f"spmap_{speaker}", person_label, speaker, person_id, 1.0, source, now, now),
    )


def upsert_segment_person_override(conn, *, segment_id: str, person_id: str, person_label: str, now: str) -> None:
    conn.execute(
        """
        insert into segment_person_overrides (segment_id, person_label, updated_at, person_id)
        values (?, ?, ?, ?)
        on conflict(segment_id) do update set
          person_label = excluded.person_label,
          updated_at = excluded.updated_at,
          person_id = excluded.person_id
        """,
        (segment_id, person_label, now, person_id),
    )
```

Then replace the inline `insert into speaker_mappings ...` and `insert into segment_person_overrides ...` blocks inside `sync_speaker_review` (`:194` and `:215`) with calls to these functions, passing `source="speaker_review"` so behavior is identical.

- [ ] **Step 4: Add web speaker routes that reuse the same writers**

Create `src/personal_context_node/web/routes_speakers.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from personal_context_node.config import AppConfig
from personal_context_node.speaker_review import upsert_segment_person_override, upsert_speaker_mapping
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


router = APIRouter(prefix="/api")


class AssignPersonRequest(BaseModel):
    person_id: str


class CreatePersonRequest(BaseModel):
    display_name: str
    person_type: str = "contact"


def _person_label(conn, *, person_id: str) -> str:
    rows = fetch_all(conn, "select display_name from persons where person_id = ?", (person_id,))
    if not rows:
        raise ValueError(f"unknown person_id: {person_id}")
    return str(rows[0]["display_name"])


@router.get("/persons")
def list_persons(request: Request) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            "select person_id, display_name, person_type, is_self from persons order by is_self desc, display_name",
        )
    finally:
        conn.close()
    return {"persons": rows}


@router.post("/persons")
def create_person(request: Request, payload: CreatePersonRequest) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    now = datetime.now(timezone.utc).isoformat()
    person_id = f"per_{uuid4().hex}"
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into persons (person_id, display_name, person_type, is_self, created_at, updated_at) values (?, ?, ?, 0, ?, ?)",
            (person_id, payload.display_name, payload.person_type, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"person_id": person_id, "display_name": payload.display_name, "person_type": payload.person_type, "is_self": 0}


@router.post("/speakers/{speaker}/assign-person")
def assign_speaker_route(request: Request, speaker: str, payload: AssignPersonRequest) -> dict[str, str]:
    config: AppConfig = request.app.state.config
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        try:
            label = _person_label(conn, person_id=payload.person_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        # speaker_clusters has a not-null label; ensure a row exists for FK-free integrity.
        conn.execute(
            "insert into speaker_clusters (speaker_cluster_id, label, source_type, source_ref, created_at) values (?, ?, ?, ?, ?) on conflict(speaker_cluster_id) do nothing",
            (speaker, speaker, "web_review", speaker, now),
        )
        upsert_speaker_mapping(conn, speaker=speaker, person_id=payload.person_id, person_label=label, now=now, source="web_review")
        conn.commit()
    finally:
        conn.close()
    return {"speaker": speaker, "person_id": payload.person_id, "person_label": label}


@router.post("/transcripts/segments/{segment_id}/person-override")
def segment_override_route(request: Request, segment_id: str, payload: AssignPersonRequest) -> dict[str, str]:
    config: AppConfig = request.app.state.config
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(config.database_path)
    try:
        initialize(conn)
        try:
            label = _person_label(conn, person_id=payload.person_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        upsert_segment_person_override(conn, segment_id=segment_id, person_id=payload.person_id, person_label=label, now=now)
        conn.commit()
    finally:
        conn.close()
    return {"segment_id": segment_id, "person_id": payload.person_id, "person_label": label}
```

> The web path writes `source="web_review"` so a later markdown publish/sync can distinguish provenance. DB is authoritative; `publish_speaker_review` projects the DB back into markdown, and `sync_speaker_review` imports markdown edits through the same writer — no second source of truth. The `/api/persons` GET/POST give the frontend the person list to assign and a way to add a new person inline (so "this is a new person" is a one-click action, not a CLI round-trip).

Add persons coverage to `tests/test_web_speaker_api.py`:

```python
def test_list_persons_includes_seeded_self(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    persons = client.get("/api/persons").json()["persons"]

    assert any(p["person_id"] == "per_paul" and p["is_self"] == 1 for p in persons)


def test_create_person_then_assign(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_person_and_segment(config.database_path)
    client = TestClient(create_app(config=config))

    created = client.post("/api/persons", json={"display_name": "Mira"})
    assert created.status_code == 200
    new_id = created.json()["person_id"]

    assigned = client.post("/api/speakers/spk_1/assign-person", json={"person_id": new_id})
    assert assigned.status_code == 200
    assert assigned.json() == {"speaker": "spk_1", "person_id": new_id, "person_label": "Mira"}
```

In `src/personal_context_node/web/app.py` register inside `create_app`:

```python
from personal_context_node.web.routes_speakers import router as speakers_router

    app.include_router(speakers_router)
```

- [ ] **Step 5: Run focused + regression tests**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_speaker_api.py tests/test_speaker_review.py -q
```

Expected: all pass (the extraction must not change `sync_speaker_review` behavior).

- [ ] **Step 6: Commit**

```bash
git add src/personal_context_node/speaker_review.py src/personal_context_node/web/routes_speakers.py src/personal_context_node/web/app.py tests/test_web_speaker_api.py
git commit -m "feat: single speaker-mapping writer shared by sync and web"
```

---

### Task 10: Segment Audio Playback

Domain helper resolves the chunk path; route is thin.

**Files:**
- Modify: `src/personal_context_node/transcription.py`
- Create: `src/personal_context_node/web/routes_audio.py`
- Modify: `src/personal_context_node/web/app.py`
- Test: `tests/test_web_audio_api.py`

- [ ] **Step 1: Write failing audio test**

Create `tests/test_web_audio_api.py`:

```python
from __future__ import annotations

import wave
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


def test_segment_audio_returns_chunk_wav(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    chunk_path = config.work_audio_dir / "2087-05-10" / "chunk.wav"
    _write_wav(chunk_path)
    _insert_segment_with_chunk(config.database_path, chunk_path.relative_to(config.data_dir))
    client = TestClient(create_app(config=config))

    response = client.get("/api/audio/segments/seg_1")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content.startswith(b"RIFF")


def test_segment_audio_missing_returns_404(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))
    assert client.get("/api/audio/segments/ghost").status_code == 404


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)


def _insert_segment_with_chunk(database_path: Path, relative_chunk_path: Path) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into audio_files (audio_file_id, source_device, source_path, source_size_bytes, source_mtime_ns, local_raw_path, sha256, duration_ms, recorded_at, imported_at, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("aud_test", "DJI Mic 3", "/source/test.wav", 1, 1, "/raw/test.wav", "sha256:test", 1000, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00", "imported"),
        )
        conn.execute(
            "insert into audio_chunks (chunk_id, audio_file_id, local_work_path, start_ms, end_ms, source_start_ms, source_end_ms, local_chunk_path, status) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("chk_1", "aud_test", str(relative_chunk_path), 0, 1000, 0, 1000, str(relative_chunk_path), "transcribed"),
        )
        conn.execute(
            "insert into transcript_segments (segment_id, audio_file_id, chunk_id, session_id, start_ms, end_ms, text, language, speaker, speaker_cluster_id, evidence_id, confidence, asr_backend, model_name, model_version, is_active, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("seg_1", "aud_test", "chk_1", "ses_1", 0, 1000, "你好", "zh", "self", "self", "ev_1", 1.0, "MockASRAdapter", "mock-asr", "test", 1, "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
```

> Confirm the real `audio_chunks` columns (`storage/sqlite.py:61`). If `local_work_path`/`local_chunk_path` differ, adjust both the fixture insert and the helper query to the actual column the work-audio path is stored in.

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_audio_api.py -q
```

Expected: 404 (route missing) — but verify the failure is the missing route, then proceed.

- [ ] **Step 3: Add the domain helper**

Add to `src/personal_context_node/transcription.py`:

```python
def segment_audio_path(*, config: AppConfig, segment_id: str) -> Path | None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select ac.local_chunk_path
            from transcript_segments ts
            join audio_chunks ac on ac.chunk_id = ts.chunk_id
            where ts.segment_id = ? and ts.is_active = 1
            """,
            (segment_id,),
        )
    finally:
        conn.close()
    if not rows or not rows[0]["local_chunk_path"]:
        return None
    path = config.data_dir / str(rows[0]["local_chunk_path"])
    return path if path.exists() else None
```

Ensure `Path`, `connect`, `fetch_all`, `initialize`, and `AppConfig` are imported in `transcription.py` (most already are; add any missing import).

- [ ] **Step 4: Add the audio route**

Create `src/personal_context_node/web/routes_audio.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from personal_context_node.config import AppConfig
from personal_context_node.transcription import segment_audio_path


router = APIRouter(prefix="/api/audio")


@router.get("/segments/{segment_id}")
def segment_audio(request: Request, segment_id: str) -> FileResponse:
    config: AppConfig = request.app.state.config
    path = segment_audio_path(config=config, segment_id=segment_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"segment audio not found: {segment_id}")
    return FileResponse(path, media_type="audio/wav")
```

In `src/personal_context_node/web/app.py` register inside `create_app`:

```python
from personal_context_node.web.routes_audio import router as audio_router

    app.include_router(audio_router)
```

- [ ] **Step 5: Run focused test**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_audio_api.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/personal_context_node/transcription.py src/personal_context_node/web/routes_audio.py src/personal_context_node/web/app.py tests/test_web_audio_api.py
git commit -m "feat: add segment audio playback api"
```

---

### Task 11: Read-Only LLM Results API

The panel must let the user *see* what the LLM produced (session summary, daily context, memory candidates) for review. These are strictly read-only: they project what the pipeline already persisted in `summaries` / `memory_candidates`. Confirm/reject of candidates stays in Obsidian (v1 boundary). SQL lives in a domain module, not the route.

**Files:**
- Create: `src/personal_context_node/llm_results.py`
- Create: `src/personal_context_node/web/routes_llm.py`
- Modify: `src/personal_context_node/web/app.py`
- Test: `tests/test_llm_results.py`, `tests/test_web_llm_api.py`

- [ ] **Step 1: Write failing domain tests**

Create `tests/test_llm_results.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from personal_context_node.config import AppConfig
from personal_context_node.llm_results import daily_context, day_memory_candidates, session_summary
from personal_context_node.storage.sqlite import connect, initialize


def test_session_summary_returns_parsed_content(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_summary(config.database_path, summary_type="session", target_id="ses_1", content={"headline": "hi", "summary": "s"})

    result = session_summary(config=config, session_id="ses_1")

    assert result is not None
    assert result["content"]["headline"] == "hi"


def test_session_summary_missing_returns_none(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
    finally:
        conn.close()
    assert session_summary(config=config, session_id="ghost") is None


def test_daily_context_and_candidates(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    _insert_summary(config.database_path, summary_type="daily", target_id="2087-05-10", content={"summary": "day"})
    _insert_candidate(config.database_path, day="2087-05-10")

    ctx = daily_context(config=config, day="2087-05-10")
    candidates = day_memory_candidates(config=config, day="2087-05-10")

    assert ctx is not None and ctx["content"]["summary"] == "day"
    assert [c["candidate_id"] for c in candidates] == ["cand_1"]


def _insert_summary(database_path: Path, *, summary_type: str, target_id: str, content: dict) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into summaries (summary_id, summary_type, target_type, target_id, prompt_version, model_name, content_json, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"sum_{target_id}", summary_type, summary_type, target_id, "v1", "rule_based", json.dumps(content), "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_candidate(database_path: Path, *, day: str) -> None:
    conn = connect(database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into memory_candidates (candidate_id, candidate_claim, claim_type, subject_json, confidence, evidence_refs_json, status, date_key, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("cand_1", "Paul 喜欢咖啡", "preference", "{}", 0.9, "[]", "pending", day, "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Run and verify failure**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_llm_results.py -q
```

Expected: `ModuleNotFoundError: No module named 'personal_context_node.llm_results'`.

- [ ] **Step 3: Implement the read-only domain helpers**

Create `src/personal_context_node/llm_results.py`:

```python
from __future__ import annotations

import json

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, fetch_all, initialize


def _latest_summary(config: AppConfig, *, summary_type: str, target_id: str) -> dict[str, object] | None:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        rows = fetch_all(
            conn,
            """
            select content_json, model_name, updated_at
            from summaries
            where summary_type = ? and target_id = ?
            order by updated_at desc
            limit 1
            """,
            (summary_type, target_id),
        )
    finally:
        conn.close()
    if not rows:
        return None
    return {
        "content": json.loads(str(rows[0]["content_json"])),
        "model_name": rows[0]["model_name"],
        "updated_at": rows[0]["updated_at"],
    }


def session_summary(*, config: AppConfig, session_id: str) -> dict[str, object] | None:
    result = _latest_summary(config, summary_type="session", target_id=session_id)
    return None if result is None else {"session_id": session_id, **result}


def daily_context(*, config: AppConfig, day: str) -> dict[str, object] | None:
    result = _latest_summary(config, summary_type="daily", target_id=day)
    return None if result is None else {"day": day, **result}


def day_memory_candidates(*, config: AppConfig, day: str) -> list[dict[str, object]]:
    conn = connect(config.database_path)
    try:
        initialize(conn)
        return fetch_all(
            conn,
            """
            select candidate_id, candidate_claim, edited_claim, claim_type, confidence, status
            from memory_candidates
            where date_key = ?
            order by created_at
            """,
            (day,),
        )
    finally:
        conn.close()
```

- [ ] **Step 4: Write failing web tests**

Create `tests/test_web_llm_api.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect, initialize
from personal_context_node.web.app import create_app


def test_session_summary_endpoint_404_when_missing(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))
    assert client.get("/api/llm/sessions/ghost/summary").status_code == 404


def test_daily_endpoint_returns_context_and_candidates(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    conn = connect(config.database_path)
    try:
        initialize(conn)
        conn.execute(
            "insert into summaries (summary_id, summary_type, target_type, target_id, prompt_version, model_name, content_json, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sum_d", "daily", "date_key", "2087-05-10", "v1", "rule_based", json.dumps({"summary": "day"}), "2087-05-10T08:00:00+08:00", "2087-05-10T08:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()
    client = TestClient(create_app(config=config))

    response = client.get("/api/llm/days/2087-05-10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["context"]["content"]["summary"] == "day"
    assert payload["memory_candidates"] == []
```

- [ ] **Step 5: Add the read-only routes**

Create `src/personal_context_node/web/routes_llm.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from personal_context_node.config import AppConfig
from personal_context_node.llm_results import daily_context, day_memory_candidates, session_summary


router = APIRouter(prefix="/api/llm")


@router.get("/sessions/{session_id}/summary")
def session_summary_route(request: Request, session_id: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    result = session_summary(config=config, session_id=session_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no session summary: {session_id}")
    return result


@router.get("/days/{day}")
def daily_route(request: Request, day: str) -> dict[str, object]:
    config: AppConfig = request.app.state.config
    return {
        "day": day,
        "context": daily_context(config=config, day=day),
        "memory_candidates": day_memory_candidates(config=config, day=day),
    }
```

In `src/personal_context_node/web/app.py` register inside `create_app`:

```python
from personal_context_node.web.routes_llm import router as llm_router

    app.include_router(llm_router)
```

- [ ] **Step 6: Run focused tests**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_llm_results.py tests/test_web_llm_api.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/personal_context_node/llm_results.py src/personal_context_node/web/routes_llm.py src/personal_context_node/web/app.py tests/test_llm_results.py tests/test_web_llm_api.py
git commit -m "feat: read-only llm results api"
```

---

### Task 12: Frontend Foundation And Typed API Client

Foundation files plus a **complete** typed client covering every endpoint the panel uses (pipeline control, status, transcript review, persons/speakers, audio, read-only LLM) and the SSE hook. App wiring happens in Task 13.

**Files:**
- Create: `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/src/test-setup.ts`, `web/index.html`, `web/src/main.tsx`, `web/src/styles.css`
- Create: `web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/api/events.ts`, `web/src/hooks/usePipelineStatus.ts`

- [ ] **Step 1: Create manifest + config**

Create `web/package.json`:

```json
{
  "name": "pcn-control-panel",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite --host 127.0.0.1",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "e2e": "playwright test"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.59.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@playwright/test": "^1.48.0",
    "@testing-library/jest-dom": "^6.6.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/user-event": "^14.5.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "jsdom": "^25.0.0",
    "typescript": "^5.6.0",
    "vite": "^5.4.0",
    "vitest": "^2.1.0"
  }
}
```

Create `web/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { host: "127.0.0.1", port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
  build: { outDir: "dist", emptyOutDir: true },
  test: { environment: "jsdom", globals: true, setupFiles: ["./src/test-setup.ts"] }
});
```

Create `web/src/test-setup.ts`:

```ts
import "@testing-library/jest-dom";
```

Create `web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["DOM", "DOM.Iterable", "ES2020"],
    "skipLibCheck": true,
    "esModuleInterop": true,
    "strict": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
```

- [ ] **Step 2: Create API types**

Create `web/src/api/types.ts`:

```ts
export type ReviewStatus = "pending_review" | "accepted" | "rejected" | "needs_fix";

export interface TranscriptSegment {
  segment_id: string;
  text: string;
  speaker: string;
  start_ms: number;
  end_ms: number;
  review_status: ReviewStatus;
  note: string | null;
}

export interface TranscriptSession {
  session_id: string;
  review_status: ReviewStatus | "blocked";
  segments: TranscriptSegment[];
}

export interface TaskRow {
  task_id: string;
  task_type: string;
  target_type: string;
  target_id: string;
  status: string;
  attempt_count: number;
  last_error: string | null;
  duration_ms: number | null;
}

export interface Person {
  person_id: string;
  display_name: string;
  person_type: string;
  is_self: number;
}

export interface StatusSnapshot {
  tasks: TaskRow[];
  worker_running: boolean;
}

export interface DailyLlmResult {
  day: string;
  context: { content: Record<string, unknown>; model_name: string | null; updated_at: string } | null;
  memory_candidates: Array<{
    candidate_id: string;
    candidate_claim: string;
    edited_claim: string | null;
    claim_type: string;
    confidence: number;
    status: string;
  }>;
}
```

- [ ] **Step 3: Create the client + SSE + hook**

Create `web/src/api/client.ts`:

```ts
import type { DailyLlmResult, Person, ReviewStatus, TaskRow, TranscriptSession } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!response.ok) throw new Error(`${init?.method ?? "GET"} ${path} failed: ${response.status}`);
  return (await response.json()) as T;
}

export const api = {
  // pipeline control
  importDir: (source_dir: string) =>
    request<{ imported_files: number; queued: boolean }>("/api/pipeline/import", { method: "POST", body: JSON.stringify({ source_dir }) }),
  run: () => request<{ worker_running: boolean }>("/api/pipeline/run", { method: "POST" }),
  stop: () => request<{ stop_requested: boolean }>("/api/pipeline/stop", { method: "POST" }),
  retry: (taskId: string) => request<{ task_id: string; status: string }>(`/api/pipeline/tasks/${taskId}/retry`, { method: "POST" }),
  // status
  statusTasks: () => request<{ tasks: TaskRow[] }>("/api/status/tasks"),
  // transcript navigation + review
  days: () => request<{ days: Array<{ day: string; session_count: number }> }>("/api/transcripts/days"),
  sessionsForDay: (day: string) =>
    request<{ day: string; sessions: Array<{ session_id: string; started_at: string; segment_count: number; review_status: string }> }>(`/api/transcripts/days/${day}/sessions`),
  session: (id: string) => request<TranscriptSession>(`/api/transcripts/sessions/${id}`),
  reviewSegment: (id: string, status: ReviewStatus, note = "") =>
    request(`/api/transcripts/segments/${id}/review`, { method: "POST", body: JSON.stringify({ status, note }) }),
  acceptRemaining: (sessionId: string) =>
    request<{ accepted: number }>(`/api/transcripts/sessions/${sessionId}/accept-remaining`, { method: "POST" }),
  // persons / speakers
  persons: () => request<{ persons: Person[] }>("/api/persons"),
  createPerson: (display_name: string) =>
    request<Person>("/api/persons", { method: "POST", body: JSON.stringify({ display_name }) }),
  assignPerson: (speaker: string, person_id: string) =>
    request(`/api/speakers/${speaker}/assign-person`, { method: "POST", body: JSON.stringify({ person_id }) }),
  overridePerson: (segmentId: string, person_id: string) =>
    request(`/api/transcripts/segments/${segmentId}/person-override`, { method: "POST", body: JSON.stringify({ person_id }) }),
  // read-only llm
  dailyLlm: (day: string) => request<DailyLlmResult>(`/api/llm/days/${day}`),
  audioUrl: (segmentId: string) => `/api/audio/segments/${segmentId}`
};
```

Create `web/src/api/events.ts`:

```ts
import type { StatusSnapshot } from "./types";

export function subscribeStatus(onSnapshot: (snap: StatusSnapshot) => void): () => void {
  const source = new EventSource("/api/events");
  source.addEventListener("status.snapshot", (event) => onSnapshot(JSON.parse((event as MessageEvent).data) as StatusSnapshot));
  return () => source.close();
}
```

Create `web/src/hooks/usePipelineStatus.ts`:

```ts
import { useEffect, useState } from "react";
import { subscribeStatus } from "../api/events";
import { api } from "../api/client";
import type { StatusSnapshot } from "../api/types";

export function usePipelineStatus(): StatusSnapshot {
  const [snapshot, setSnapshot] = useState<StatusSnapshot>({ tasks: [], worker_running: false });
  useEffect(() => {
    let active = true;
    // Seed from a one-shot GET so the UI is populated before the first SSE frame.
    api.statusTasks().then((r) => active && setSnapshot((s) => ({ ...s, tasks: r.tasks }))).catch(() => undefined);
    const unsubscribe = subscribeStatus((snap) => active && setSnapshot(snap));
    return () => {
      active = false;
      unsubscribe();
    };
  }, []);
  return snapshot;
}
```

- [ ] **Step 4: Create shell HTML + entry + styles**

Create `web/index.html`:

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Personal Context Node</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Create `web/src/main.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
);
```

Create `web/src/styles.css`:

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; color: #1f2933; background: #f6f7f9; }
.workbench { display: grid; grid-template-columns: 200px minmax(0, 1fr) 320px; min-height: 100vh; }
.pipeline-rail, .run-inspector { border-right: 1px solid #d9dee7; background: #fff; padding: 16px; }
.run-inspector { border-left: 1px solid #d9dee7; border-right: 0; }
.main-panel { padding: 20px; }
.stage { padding: 6px 8px; border-radius: 6px; }
.stage.active { font-weight: 600; background: #eef2ff; }
.segment-row { display: grid; gap: 6px; padding: 10px; border-bottom: 1px solid #eef1f5; }
.speaker-chip { font-size: 12px; color: #475569; }
.task-list { font-size: 13px; }
.task-row { display: flex; gap: 8px; justify-content: space-between; padding: 4px 0; }
.candidate { padding: 8px; border: 1px solid #e2e8f0; border-radius: 6px; margin-bottom: 6px; }
```

> `App.tsx` is created in Task 13 (the live container). `npm install` + `npm run build` is run at the end of Task 13 once `App.tsx` exists.

- [ ] **Step 5: Commit**

```bash
git add web/package.json web/vite.config.ts web/tsconfig.json web/index.html web/src/main.tsx web/src/styles.css web/src/test-setup.ts web/src/api web/src/hooks
git commit -m "feat: add frontend foundation and typed api client"
```

---

### Task 13: Live App Container — Import, Run, Stop, Live Status

Replaces the static shell. The container owns the source-dir input, the Import/Run/Stop actions, the live SSE status (tasks + worker state), the task list, and the pipeline-rail stage derived from task state. This is the wiring the review flagged as missing.

**Files:**
- Create: `web/src/App.tsx`, `web/src/components/PipelineRail.tsx`, `web/src/components/RunInspector.tsx`, `web/src/components/TaskList.tsx`, `web/src/lib/stages.ts`
- Test: `web/src/__tests__/{PipelineRail,RunInspector,App}.test.tsx`

- [ ] **Step 1: Write component + container tests**

Create `web/src/__tests__/PipelineRail.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineRail } from "../components/PipelineRail";

describe("PipelineRail", () => {
  it("renders the six stages and marks the active one", () => {
    render(<PipelineRail activeStage="asr" />);
    for (const label of ["Device", "Import", "ASR", "Transcript Review", "LLM", "Publish"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("ASR").className).toContain("active");
  });
});
```

Create `web/src/__tests__/RunInspector.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunInspector } from "../components/RunInspector";

describe("RunInspector", () => {
  it("disables Run while the worker is running and enables Stop", () => {
    render(<RunInspector workerRunning={true} taskCount={3} onRun={() => undefined} onStop={() => undefined} />);
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
  });
});
```

Create `web/src/__tests__/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";

describe("App container", () => {
  beforeEach(() => {
    // EventSource is not in jsdom; stub it so usePipelineStatus mounts cleanly.
    vi.stubGlobal("EventSource", class {
      addEventListener() {}
      close() {}
    } as unknown as typeof EventSource);
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }), { status: 200 });
      if (url === "/api/pipeline/import") return new Response(JSON.stringify({ imported_files: 1, queued: true }), { status: 200 });
      if (url === "/api/pipeline/run") return new Response(JSON.stringify({ worker_running: true }), { status: 200 });
      return new Response("{}", { status: 200 });
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("imports the entered directory then starts a run", async () => {
    render(<App />);
    await userEvent.type(screen.getByLabelText("Source directory"), "/data/incoming");
    await userEvent.click(screen.getByRole("button", { name: "Import" }));

    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContain("/api/pipeline/import");
    expect(calls).toContain("/api/pipeline/run");
  });
});
```

- [ ] **Step 2: Add stage mapping**

Create `web/src/lib/stages.ts`:

```ts
import type { TaskRow } from "../api/types";

export type Stage = "device" | "import" | "asr" | "review" | "llm" | "publish";

export const STAGES: Array<{ id: Stage; label: string }> = [
  { id: "device", label: "Device" },
  { id: "import", label: "Import" },
  { id: "asr", label: "ASR" },
  { id: "review", label: "Transcript Review" },
  { id: "llm", label: "LLM" },
  { id: "publish", label: "Publish" }
];

export function stageForTaskType(taskType: string): Stage {
  if (taskType === "vad" || taskType === "asr") return "asr";
  if (taskType === "session_derive" || taskType === "summarize_session" || taskType === "daily_generate") return "llm";
  if (taskType === "obsidian_publish" || taskType === "archive") return "publish";
  return "import";
}

export function activeStage(tasks: TaskRow[]): Stage {
  const live = tasks.find((t) => t.status === "running") ?? tasks.find((t) => t.status === "pending");
  return live ? stageForTaskType(live.task_type) : "device";
}
```

- [ ] **Step 3: Add components**

Create `web/src/components/PipelineRail.tsx`:

```tsx
import type { Stage } from "../lib/stages";
import { STAGES } from "../lib/stages";

export function PipelineRail({ activeStage }: { activeStage: Stage }) {
  return (
    <nav aria-label="Pipeline stages">
      {STAGES.map((stage) => (
        <div className={stage.id === activeStage ? "stage active" : "stage"} key={stage.id}>
          {stage.label}
        </div>
      ))}
    </nav>
  );
}
```

Create `web/src/components/RunInspector.tsx`:

```tsx
export function RunInspector({
  workerRunning,
  taskCount,
  onRun,
  onStop
}: {
  workerRunning: boolean;
  taskCount: number;
  onRun: () => void;
  onStop: () => void;
}) {
  return (
    <aside className="run-inspector">
      <h2>Run Inspector</h2>
      <p>{workerRunning ? "Running" : "Idle"}</p>
      <p>{taskCount} tasks</p>
      <button onClick={onRun} disabled={workerRunning}>Run</button>
      <button onClick={onStop} disabled={!workerRunning}>Stop</button>
    </aside>
  );
}
```

Create `web/src/components/TaskList.tsx`:

```tsx
import type { TaskRow } from "../api/types";

export function TaskList({ tasks, onRetry }: { tasks: TaskRow[]; onRetry: (taskId: string) => void }) {
  return (
    <div className="task-list">
      {tasks.map((task) => (
        <div className="task-row" key={task.task_id}>
          <span>{task.task_type}</span>
          <span>{task.status}</span>
          {task.status.startsWith("failed") ? (
            <button onClick={() => onRetry(task.task_id)}>Retry</button>
          ) : null}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Add the live container**

Create `web/src/App.tsx`:

```tsx
import { useState } from "react";
import { api } from "./api/client";
import { PipelineRail } from "./components/PipelineRail";
import { RunInspector } from "./components/RunInspector";
import { TaskList } from "./components/TaskList";
import { usePipelineStatus } from "./hooks/usePipelineStatus";
import { activeStage } from "./lib/stages";

export function App() {
  const { tasks, worker_running } = usePipelineStatus();
  const [sourceDir, setSourceDir] = useState("");

  async function handleImport() {
    if (!sourceDir) return;
    await api.importDir(sourceDir);
    await api.run(); // default import enqueues only; explicitly start the background worker
  }

  return (
    <main className="workbench">
      <aside className="pipeline-rail">
        <PipelineRail activeStage={activeStage(tasks)} />
      </aside>
      <section className="main-panel">
        <h1>Personal Context Node</h1>
        <label>
          Source directory
          <input value={sourceDir} onChange={(event) => setSourceDir(event.target.value)} placeholder="/path/to/recordings" />
        </label>
        <button onClick={handleImport}>Import</button>
        <h2>Tasks</h2>
        <TaskList tasks={tasks} onRetry={(taskId) => api.retry(taskId)} />
      </section>
      <RunInspector
        workerRunning={worker_running}
        taskCount={tasks.length}
        onRun={() => api.run()}
        onStop={() => api.stop()}
      />
    </main>
  );
}
```

- [ ] **Step 5: Install, test, build**

```bash
cd web && npm install && npm test && npm run build
```

Expected: deps install; the three test files pass; `vite build` writes `dist/`.

- [ ] **Step 6: Commit**

```bash
git add web/src/App.tsx web/src/components web/src/lib web/src/__tests__
git commit -m "feat: live control-panel container wired to pipeline status"
```

---

### Task 14: Transcript Review And Speaker/Person Panels

The "who said this / is this the same person" loop, end to end in the browser: review segments, play audio, assign a speaker to a person (merge = two speakers → one person), override a single segment's person, and create a new person inline.

**Files:**
- Create: `web/src/features/transcript/SegmentRow.tsx`, `web/src/features/transcript/TranscriptReviewPanel.tsx`, `web/src/features/speakers/SpeakerPanel.tsx`
- Test: `web/src/__tests__/{TranscriptReviewPanel,SpeakerPanel}.test.tsx`

- [ ] **Step 1: Write component tests**

Create `web/src/__tests__/TranscriptReviewPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TranscriptReviewPanel } from "../features/transcript/TranscriptReviewPanel";

const session = {
  session_id: "ses_1",
  review_status: "pending_review" as const,
  segments: [{ segment_id: "seg_1", text: "你好", speaker: "spk_1", start_ms: 0, end_ms: 1000, review_status: "pending_review" as const, note: null }]
};

describe("TranscriptReviewPanel", () => {
  it("accepts a segment and overrides its person", async () => {
    const onReview = vi.fn();
    const onOverride = vi.fn();
    render(
      <TranscriptReviewPanel
        session={session}
        persons={[{ person_id: "per_paul", display_name: "Paul", person_type: "self", is_self: 1 }]}
        onReview={onReview}
        onOverride={onOverride}
        onPlay={() => undefined}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(onReview).toHaveBeenCalledWith("seg_1", "accepted");

    await userEvent.selectOptions(screen.getByLabelText("Override person for seg_1"), "per_paul");
    expect(onOverride).toHaveBeenCalledWith("seg_1", "per_paul");
  });
});
```

Create `web/src/__tests__/SpeakerPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SpeakerPanel } from "../features/speakers/SpeakerPanel";

describe("SpeakerPanel", () => {
  it("assigns a speaker to a chosen person", async () => {
    const onAssign = vi.fn();
    render(
      <SpeakerPanel
        speakers={["spk_1"]}
        persons={[{ person_id: "per_paul", display_name: "Paul", person_type: "self", is_self: 1 }]}
        onAssign={onAssign}
        onCreatePerson={async () => undefined}
      />
    );
    await userEvent.selectOptions(screen.getByLabelText("Person for spk_1"), "per_paul");
    expect(onAssign).toHaveBeenCalledWith("spk_1", "per_paul");
  });
});
```

- [ ] **Step 2: Add SegmentRow with play + person override**

Create `web/src/features/transcript/SegmentRow.tsx`:

```tsx
import type { Person, ReviewStatus, TranscriptSegment } from "../../api/types";

export function SegmentRow({
  segment,
  persons,
  onReview,
  onOverride,
  onPlay
}: {
  segment: TranscriptSegment;
  persons: Person[];
  onReview: (segmentId: string, status: ReviewStatus) => void;
  onOverride: (segmentId: string, personId: string) => void;
  onPlay: (segmentId: string) => void;
}) {
  return (
    <article className="segment-row">
      <div>
        <button aria-label="Play segment" onClick={() => onPlay(segment.segment_id)}>Play</button>
        <span className="speaker-chip">{segment.speaker}</span>
        <time>{formatMs(segment.start_ms)}-{formatMs(segment.end_ms)}</time>
        <span>{segment.review_status}</span>
      </div>
      <p>{segment.text}</p>
      <div>
        <button onClick={() => onReview(segment.segment_id, "accepted")}>Accept</button>
        <button onClick={() => onReview(segment.segment_id, "rejected")}>Reject</button>
        <button onClick={() => onReview(segment.segment_id, "needs_fix")}>Flag</button>
        <select
          aria-label={`Override person for ${segment.segment_id}`}
          defaultValue=""
          onChange={(event) => event.target.value && onOverride(segment.segment_id, event.target.value)}
        >
          <option value="" disabled>Override person…</option>
          {persons.map((person) => (
            <option key={person.person_id} value={person.person_id}>{person.display_name}</option>
          ))}
        </select>
      </div>
    </article>
  );
}

function formatMs(value: number) {
  const seconds = Math.floor(value / 1000);
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}
```

- [ ] **Step 3: Add the transcript panel**

Create `web/src/features/transcript/TranscriptReviewPanel.tsx`:

```tsx
import type { Person, ReviewStatus, TranscriptSession } from "../../api/types";
import { SegmentRow } from "./SegmentRow";

export function TranscriptReviewPanel({
  session,
  persons,
  onReview,
  onOverride,
  onPlay
}: {
  session: TranscriptSession;
  persons: Person[];
  onReview: (segmentId: string, status: ReviewStatus) => void;
  onOverride: (segmentId: string, personId: string) => void;
  onPlay: (segmentId: string) => void;
}) {
  return (
    <section>
      <header className="panel-header">
        <h2>{session.session_id}</h2>
        <span>{session.review_status}</span>
      </header>
      <div className="segment-list">
        {session.segments.map((segment) => (
          <SegmentRow
            key={segment.segment_id}
            segment={segment}
            persons={persons}
            onReview={onReview}
            onOverride={onOverride}
            onPlay={onPlay}
          />
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Add the speaker panel**

Create `web/src/features/speakers/SpeakerPanel.tsx`:

```tsx
import { useState } from "react";
import type { Person } from "../../api/types";

export function SpeakerPanel({
  speakers,
  persons,
  onAssign,
  onCreatePerson
}: {
  speakers: string[];
  persons: Person[];
  onAssign: (speaker: string, personId: string) => void;
  onCreatePerson: (displayName: string) => Promise<void>;
}) {
  const [newName, setNewName] = useState("");
  return (
    <section>
      <h2>Speakers</h2>
      {speakers.map((speaker) => (
        <div key={speaker}>
          <label>
            {`Person for ${speaker}`}
            <select
              aria-label={`Person for ${speaker}`}
              defaultValue=""
              onChange={(event) => event.target.value && onAssign(speaker, event.target.value)}
            >
              <option value="" disabled>Assign person…</option>
              {persons.map((person) => (
                <option key={person.person_id} value={person.person_id}>{person.display_name}</option>
              ))}
            </select>
          </label>
        </div>
      ))}
      <div>
        <input aria-label="New person name" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="New person" />
        <button onClick={() => newName && onCreatePerson(newName)}>Add person</button>
      </div>
    </section>
  );
}
```

> Merging two speakers is expressed as assigning both to the same `person_id` — no separate "merge" endpoint, consistent with the single-writer model in Task 9.

- [ ] **Step 5: Run frontend tests**

```bash
cd web && npm test
```

Expected: transcript + speaker tests pass (plus the Task 13 tests).

- [ ] **Step 6: Commit**

```bash
git add web/src/features/transcript web/src/features/speakers web/src/__tests__/TranscriptReviewPanel.test.tsx web/src/__tests__/SpeakerPanel.test.tsx
git commit -m "feat: transcript review and speaker/person panels"
```

---

### Task 15: Read-Only LLM Result Panel

Show what the LLM produced for a day — daily context summary and memory candidates — strictly read-only, with an explicit pointer that candidate sign-off happens in Obsidian. This closes the "I can see the LLM output" half of the loop without creating a second candidate-mutation path.

**Files:**
- Create: `web/src/features/llm/LlmResultPanel.tsx`
- Test: `web/src/__tests__/LlmResultPanel.test.tsx`

- [ ] **Step 1: Write component test**

Create `web/src/__tests__/LlmResultPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LlmResultPanel } from "../features/llm/LlmResultPanel";

describe("LlmResultPanel", () => {
  it("renders daily summary and read-only candidates with an Obsidian pointer", () => {
    render(
      <LlmResultPanel
        result={{
          day: "2087-05-10",
          context: { content: { summary: "今天讨论了部署" }, model_name: "rule_based", updated_at: "2087-05-10T09:00:00+08:00" },
          memory_candidates: [
            { candidate_id: "cand_1", candidate_claim: "Paul 喜欢咖啡", edited_claim: null, claim_type: "preference", confidence: 0.9, status: "pending" }
          ]
        }}
      />
    );
    expect(screen.getByText("今天讨论了部署")).toBeInTheDocument();
    expect(screen.getByText("Paul 喜欢咖啡")).toBeInTheDocument();
    expect(screen.getByText(/Obsidian/)).toBeInTheDocument();
    // Read-only: no confirm/reject controls.
    expect(screen.queryByRole("button", { name: /confirm/i })).toBeNull();
  });
});
```

- [ ] **Step 2: Add the panel**

Create `web/src/features/llm/LlmResultPanel.tsx`:

```tsx
import type { DailyLlmResult } from "../../api/types";

export function LlmResultPanel({ result }: { result: DailyLlmResult }) {
  const summary = result.context?.content?.["summary"];
  return (
    <section>
      <h2>LLM Result — {result.day}</h2>
      {summary ? <p>{String(summary)}</p> : <p>No daily context generated yet.</p>}
      <h3>Memory candidates (read-only)</h3>
      <p>Confirm or reject these in Obsidian — the panel shows them for review only.</p>
      {result.memory_candidates.map((candidate) => (
        <div className="candidate" key={candidate.candidate_id}>
          <strong>{candidate.edited_claim ?? candidate.candidate_claim}</strong>
          <span> · {candidate.claim_type} · {Math.round(candidate.confidence * 100)}% · {candidate.status}</span>
        </div>
      ))}
    </section>
  );
}
```

- [ ] **Step 3: Run frontend tests**

```bash
cd web && npm test
```

Expected: all frontend tests pass.

- [ ] **Step 4: Commit**

```bash
git add web/src/features/llm web/src/__tests__/LlmResultPanel.test.tsx
git commit -m "feat: read-only llm result panel"
```

---

### Task 16: Compose The Review Workspace Into App

The panels from Tasks 13–15 now get composed into a working surface. This is the navigation the user chose: **by-day → session list**. Selecting a day loads its sessions and that day's LLM result; selecting a session loads its transcript + persons and renders the transcript and speaker panels wired to the API. Without this task the panels exist but are unreachable, and the full-chain e2e has nothing to drive.

**Files:**
- Create: `web/src/features/workspace/WorkspaceNav.tsx`
- Modify: `web/src/App.tsx` (compose nav + panels; the Task 13 container becomes the control header of this surface)
- Test: `web/src/__tests__/WorkspaceNav.test.tsx`, extend `web/src/__tests__/App.test.tsx`

- [ ] **Step 1: Write the navigation test**

Create `web/src/__tests__/WorkspaceNav.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkspaceNav } from "../features/workspace/WorkspaceNav";

describe("WorkspaceNav", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [{ day: "2087-05-10", session_count: 2 }] }), { status: 200 });
      if (url === "/api/transcripts/days/2087-05-10/sessions")
        return new Response(JSON.stringify({ day: "2087-05-10", sessions: [{ session_id: "ses_1", started_at: "", segment_count: 3, review_status: "pending_review" }] }), { status: 200 });
      return new Response("{}", { status: 200 });
    }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists days, then lists sessions for the selected day", async () => {
    const onSelectDay = vi.fn();
    const onSelectSession = vi.fn();
    const { rerender } = render(<WorkspaceNav selectedDay={null} onSelectDay={onSelectDay} onSelectSession={onSelectSession} />);

    await waitFor(() => expect(screen.getByRole("button", { name: /2087-05-10/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /2087-05-10/ }));
    expect(onSelectDay).toHaveBeenCalledWith("2087-05-10");

    rerender(<WorkspaceNav selectedDay="2087-05-10" onSelectDay={onSelectDay} onSelectSession={onSelectSession} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /ses_1/ })).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /ses_1/ }));
    expect(onSelectSession).toHaveBeenCalledWith("ses_1");
  });
});
```

- [ ] **Step 2: Add `WorkspaceNav`**

Create `web/src/features/workspace/WorkspaceNav.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api } from "../../api/client";

export function WorkspaceNav({
  selectedDay,
  onSelectDay,
  onSelectSession
}: {
  selectedDay: string | null;
  onSelectDay: (day: string) => void;
  onSelectSession: (sessionId: string) => void;
}) {
  const [days, setDays] = useState<Array<{ day: string; session_count: number }>>([]);
  const [sessions, setSessions] = useState<Array<{ session_id: string; review_status: string }>>([]);

  useEffect(() => {
    api.days().then((r) => setDays(r.days ?? [])).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!selectedDay) return;
    api.sessionsForDay(selectedDay).then((r) => setSessions(r.sessions ?? [])).catch(() => undefined);
  }, [selectedDay]);

  return (
    <nav aria-label="Days and sessions">
      <h3>Days</h3>
      {days.map((d) => (
        <button key={d.day} className={d.day === selectedDay ? "day active" : "day"} onClick={() => onSelectDay(d.day)}>
          {d.day} ({d.session_count})
        </button>
      ))}
      {selectedDay ? (
        <>
          <h3>Sessions</h3>
          {sessions.map((s) => (
            <button key={s.session_id} onClick={() => onSelectSession(s.session_id)}>
              {s.session_id} · {s.review_status}
            </button>
          ))}
        </>
      ) : null}
    </nav>
  );
}
```

- [ ] **Step 3: Compose nav + panels into `App.tsx`**

Replace `web/src/App.tsx` with the composed surface (keeps the Task 13 control header; adds navigation and the three panels):

```tsx
import { useEffect, useState } from "react";
import { api } from "./api/client";
import { PipelineRail } from "./components/PipelineRail";
import { RunInspector } from "./components/RunInspector";
import { TaskList } from "./components/TaskList";
import { WorkspaceNav } from "./features/workspace/WorkspaceNav";
import { TranscriptReviewPanel } from "./features/transcript/TranscriptReviewPanel";
import { SpeakerPanel } from "./features/speakers/SpeakerPanel";
import { LlmResultPanel } from "./features/llm/LlmResultPanel";
import { usePipelineStatus } from "./hooks/usePipelineStatus";
import { activeStage } from "./lib/stages";
import type { DailyLlmResult, Person, TranscriptSession } from "./api/types";

export function App() {
  const { tasks, worker_running } = usePipelineStatus();
  const [sourceDir, setSourceDir] = useState("");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [session, setSession] = useState<TranscriptSession | null>(null);
  const [persons, setPersons] = useState<Person[]>([]);
  const [llm, setLlm] = useState<DailyLlmResult | null>(null);

  useEffect(() => {
    api.persons().then((r) => setPersons(r.persons ?? [])).catch(() => undefined);
  }, []);

  async function handleImport() {
    if (!sourceDir) return;
    await api.importDir(sourceDir);
    await api.run(); // default import enqueues only; explicitly start the background worker
  }

  async function selectDay(day: string) {
    setSelectedDay(day);
    setLlm(await api.dailyLlm(day));
  }

  async function reloadSession() {
    if (session) setSession(await api.session(session.session_id));
  }

  const speakers = session ? Array.from(new Set(session.segments.map((s) => s.speaker))) : [];

  return (
    <main className="workbench">
      <aside className="pipeline-rail">
        <PipelineRail activeStage={activeStage(tasks)} />
        <WorkspaceNav selectedDay={selectedDay} onSelectDay={selectDay} onSelectSession={async (id) => setSession(await api.session(id))} />
      </aside>
      <section className="main-panel">
        <h1>Personal Context Node</h1>
        <label>
          Source directory
          <input value={sourceDir} onChange={(event) => setSourceDir(event.target.value)} placeholder="/path/to/recordings" />
        </label>
        <button onClick={handleImport}>Import</button>
        <TaskList tasks={tasks} onRetry={(taskId) => api.retry(taskId)} />
        {session ? (
          <TranscriptReviewPanel
            session={session}
            persons={persons}
            onReview={async (id, status) => { await api.reviewSegment(id, status); await reloadSession(); }}
            onOverride={async (id, personId) => { await api.overridePerson(id, personId); await reloadSession(); }}
            onPlay={(id) => { void new Audio(api.audioUrl(id)).play(); }}
          />
        ) : null}
        {session ? (
          <SpeakerPanel
            speakers={speakers}
            persons={persons}
            onAssign={async (speaker, personId) => { await api.assignPerson(speaker, personId); await reloadSession(); }}
            onCreatePerson={async (name) => { await api.createPerson(name); setPersons((await api.persons()).persons ?? []); }}
          />
        ) : null}
        {llm ? <LlmResultPanel result={llm} /> : null}
      </section>
      <RunInspector workerRunning={worker_running} taskCount={tasks.length} onRun={() => api.run()} onStop={() => api.stop()} />
    </main>
  );
}
```

- [ ] **Step 4: Extend the App integration test**

Append to `web/src/__tests__/App.test.tsx` a flow test that navigates day → session → accept. Extend the `beforeEach` fetch stub to also answer `/api/persons`, `/api/transcripts/days`, `/api/transcripts/days/{day}/sessions`, and `/api/transcripts/sessions/{id}`:

```tsx
it("navigates day -> session and accepts a segment", async () => {
  (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }), { status: 200 });
    if (url === "/api/persons") return new Response(JSON.stringify({ persons: [{ person_id: "per_paul", display_name: "Paul", person_type: "self", is_self: 1 }] }), { status: 200 });
    if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [{ day: "2087-05-10", session_count: 1 }] }), { status: 200 });
    if (url === "/api/transcripts/days/2087-05-10/sessions") return new Response(JSON.stringify({ day: "2087-05-10", sessions: [{ session_id: "ses_1", started_at: "", segment_count: 1, review_status: "pending_review" }] }), { status: 200 });
    if (url === "/api/llm/days/2087-05-10") return new Response(JSON.stringify({ day: "2087-05-10", context: null, memory_candidates: [] }), { status: 200 });
    if (url === "/api/transcripts/sessions/ses_1") return new Response(JSON.stringify({ session_id: "ses_1", review_status: "pending_review", segments: [{ segment_id: "seg_1", text: "你好", speaker: "spk_1", start_ms: 0, end_ms: 1000, review_status: "pending_review", note: null }] }), { status: 200 });
    if (url === "/api/transcripts/segments/seg_1/review") return new Response(JSON.stringify({ segment_id: "seg_1", status: "accepted" }), { status: 200 });
    return new Response("{}", { status: 200 });
  });

  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: /2087-05-10/ }));
  await userEvent.click(await screen.findByRole("button", { name: /ses_1/ }));
  await userEvent.click(await screen.findByRole("button", { name: "Accept" }));

  const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
  expect(calls).toContain("/api/transcripts/sessions/ses_1");
  expect(calls).toContain("/api/transcripts/segments/seg_1/review");
});
```

> The original Task 13 App test still passes because `WorkspaceNav`/`persons` loads tolerate the `{}` fallback (`?? []`).

- [ ] **Step 5: Run frontend tests + build**

```bash
cd web && npm test && npm run build
```

Expected: all frontend tests pass; build succeeds.

- [ ] **Step 6: Commit**

```bash
git add web/src/features/workspace web/src/App.tsx web/src/__tests__/WorkspaceNav.test.tsx web/src/__tests__/App.test.tsx
git commit -m "feat: compose day/session navigation and review panels into app"
```

---

### Task 17: Static Mount And Full-Chain End-To-End

**Files:**
- Modify: `src/personal_context_node/web/app.py`
- Test: extend `tests/test_web_status_api.py`, create `tests/test_web_e2e.py`, `web/e2e/control-panel.spec.ts`

- [ ] **Step 1: Write failing root test**

Append to `tests/test_web_status_api.py`:

```python
def test_root_returns_api_marker_when_frontend_not_built(tmp_path: Path) -> None:
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    client = TestClient(create_app(config=config))
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["app"] == "Personal Context Node"
```

- [ ] **Step 2: Add root + conditional static mount**

In `src/personal_context_node/web/app.py`, inside `create_app` after routers:

```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles

    @app.get("/")
    def root() -> dict[str, str]:
        return {"app": "Personal Context Node", "mode": "api-only"}

    dist_dir = Path(__file__).resolve().parents[3] / "web" / "dist"
    if dist_dir.exists():
        app.mount("/app", StaticFiles(directory=dist_dir, html=True), name="frontend")
```

> Mount the SPA at `/app` so it never shadows `/api/*` or the JSON root. Dev uses `http://127.0.0.1:5173` (Vite proxies `/api`); built deployment opens `http://127.0.0.1:8765/app`.

- [ ] **Step 3: Backend E2E (queue drives end to end with mock backends)**

Create `tests/test_web_e2e.py`:

```python
from __future__ import annotations

import math
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from personal_context_node.config import AppConfig
from personal_context_node.web.app import create_app


def test_import_wait_then_review_then_status_smoke(tmp_path: Path) -> None:
    source = tmp_path / "NO NAME"
    _write_wav(source / "TX02_MIC001_20870510_173550_orig.wav")
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault", vad_backend="mock", asr_backend="mock", llm_backend="mock")
    client = TestClient(create_app(config=config))

    imported = client.post("/api/pipeline/import", json={"source_dir": str(source), "wait": True})
    assert imported.status_code == 200
    assert imported.json()["imported_files"] == 1

    tasks = client.get("/api/status/tasks").json()["tasks"]
    assert any(row["status"] == "succeeded" for row in tasks)


def _write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        frames = bytearray()
        for index in range(16000):
            sample = int(10000 * math.sin(2 * math.pi * 440 * index / 16000))
            frames.extend(sample.to_bytes(2, byteorder="little", signed=True))
        wav.writeframes(bytes(frames))
```

- [ ] **Step 4: Run backend tests**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest tests/test_web_status_api.py tests/test_web_e2e.py -q
```

Expected: all pass.

- [ ] **Step 5: Full-chain Playwright spec (real UI operations)**

This drives the actual control panel through the whole loop. It assumes the backend runs with mock backends against a seeded sample directory so a deterministic session exists after import.

Create `web/e2e/control-panel.spec.ts`:

```ts
import { expect, test } from "@playwright/test";

const PANEL = "http://127.0.0.1:5173";
const SAMPLE_DIR = process.env.PCN_E2E_SOURCE_DIR ?? "sample_data/e2e";

test("import -> run -> status -> transcript accept -> speaker assign -> llm result", async ({ page }) => {
  await page.goto(PANEL);

  // 1. Import + run.
  await page.getByLabel("Source directory").fill(SAMPLE_DIR);
  await page.getByRole("button", { name: "Import" }).click();

  // 2. SSE-driven status reaches a succeeded task (mock backends complete fast).
  await expect(page.locator(".task-row", { hasText: "succeeded" }).first()).toBeVisible({ timeout: 30_000 });

  // 3. Navigate by day -> session (the chosen navigation model). Selecting a day also loads its LLM result.
  await page.getByRole("button", { name: /^2087-/ }).first().click();
  await page.getByRole("button", { name: /^ses_/ }).first().click();

  // 4. Accept a transcript segment in the loaded session.
  await page.getByRole("button", { name: "Accept" }).first().click();

  // 5. Speaker -> person assignment.
  await page.getByLabel(/^Person for /).first().selectOption({ index: 1 });

  // 6. Read-only LLM result is visible with the Obsidian pointer.
  await expect(page.getByText(/Memory candidates \(read-only\)/)).toBeVisible();
  await expect(page.getByText(/Confirm or reject these in Obsidian/)).toBeVisible();
});
```

> Run it against a live stack:
> ```bash
> UV_CACHE_DIR=.tmp/uv-cache uv run pcn web --data-dir .tmp/web-e2e/data --obsidian-vault .tmp/web-e2e/vault   # terminal 1, mock backends configured
> cd web && npm run dev                                                                                          # terminal 2
> PCN_E2E_SOURCE_DIR=$(pwd)/sample_data/e2e npm run e2e                                                           # terminal 3
> ```
> The spec is the acceptance gate for "the loop closes in the browser." It requires the transcript/speaker/LLM panels to be reachable from `App.tsx`; if a panel is opened via navigation rather than always-rendered, add the corresponding click before its assertion when wiring those panels into `App.tsx`. Keep it green.

- [ ] **Step 6: Commit**

```bash
git add src/personal_context_node/web/app.py tests/test_web_status_api.py tests/test_web_e2e.py web/e2e/control-panel.spec.ts
git commit -m "test: static mount and full-chain control-panel e2e"
```

---

### Task 18: Documentation And Final Verification

**Files:**
- Create: `docs/local-web-control-panel.md`
- Modify: `SYSTEM_DESIGN_CN.md`

- [ ] **Step 1: Add user-facing doc**

Create `docs/local-web-control-panel.md`:

```markdown
# Local Web Control Panel

Localhost-only panel bound to `127.0.0.1`. It is a thin observer + one extra worker over the existing task queue — it does not replace the CLI, launchd automation, or Obsidian.

## Start

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pcn web --config config/local.toml
```

Dev frontend: `cd web && npm run dev` then open `http://127.0.0.1:5173`.
Built frontend: open `http://127.0.0.1:8765/app`.

## Flow in the panel

1. Enter the recordings directory and click **Import** — files are imported and `vad` tasks enqueued; the panel then starts the background worker (or click **Run** later).
2. Watch live task status (SSE) and the pipeline rail; **Stop** requests a cooperative stop between work units.
3. **Transcript Review** — play a segment, accept/reject/flag it, and correct attribution.
4. **Speakers** — assign a speaker to a person (assigning two speakers to one person = merge), override a single segment's person, or add a new person inline.
5. **LLM Result** — read the daily context summary and memory candidates (read-only).

## How it relates to the rest of the system

- Starting a run drives the *same* `process_once` loop as `pcn run-all` and launchd. Lease-based claiming makes the web worker safe to run alongside launchd.
- Run history and live task state come from the existing `job_runs` and `tasks` tables — there is no separate run table.

## Acceptance gate

- `require_accepted_transcripts` defaults to `false`. With it off, autonomous launchd runs are unchanged.
- Set it `true` under `[llm]` to require that only `accepted` transcript segments feed session summaries and daily context (human-in-the-loop).

## What the v1 panel can and cannot accept

- **In the panel:** accept/reject/flag transcript segments; correct speaker→person attribution.
- **Read-only in the panel:** LLM session summary, daily context, and memory candidates.
- **Still in Obsidian:** final confirm/reject/edit of memory candidates (`confirm_checked_candidates`). A second HTTP mutation path would create a competing source of truth, so it is deferred.

## Boundaries

- Audio and raw transcripts stay local.
- Obsidian remains the final knowledge surface and the candidate control surface in v1.
- Speaker→person edits write the database through the same writer the markdown sync uses; the database is authoritative.
```

- [ ] **Step 2: Update design doc**

In `SYSTEM_DESIGN_CN.md` section `1.2 非目标`, replace the line `2. v1 不做 Web 审核 UI。` with:

```markdown
2. 初始 CLI v1 不做 Web 审核 UI;本机 Web Control Panel 作为后续本地控制台阶段实现,它是既有任务队列之上的薄观察层 + 一个额外 worker,不引入第二套编排,默认不改变自治行为(`require_accepted_transcripts` 默认关闭)。音频、原始转写与统计仍本地化。
```

Add after the architecture section:

```markdown
### 2.2 本机 Web Control Panel 阶段

Web 层只做加法:复用 `tasks` 队列 + `process_once` 作为唯一编排器,run 身份与历史来自既有 `job_runs`,实时状态来自 `tasks`;停止是进程内协作式标志,在工作单元之间检查。LLM 验收闸门是可选项(`require_accepted_transcripts`,默认关闭),其谓词只存在于 `transcript_review.accepted_segments_clause` 一处;speaker→person 由 `speaker_review` 的单一写入函数承担,markdown 同步与 Web API 共用它,数据库为权威源。前端覆盖 transcript 验收与 speaker/person 纠正,并只读展示 LLM 结果;memory candidate 的最终确认仍在 Obsidian。
```

- [ ] **Step 3: Full backend suite**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run pytest
```

Expected: all pass.

- [ ] **Step 4: Frontend tests + build**

```bash
cd web && npm test && npm run build
```

Expected: all pass; build succeeds.

- [ ] **Step 5: Lint**

```bash
UV_CACHE_DIR=.tmp/uv-cache uv run --with ruff ruff check .
```

Expected: All checks passed.

- [ ] **Step 6: Commit**

```bash
git add docs/local-web-control-panel.md SYSTEM_DESIGN_CN.md
git commit -m "docs: document local web control panel (v2)"
```

---

## Acceptance Criteria

- `pcn web` starts a FastAPI server bound to `127.0.0.1`; `--host` other than `127.0.0.1` is rejected.
- **No `pipeline_runs` table is created.** Run state comes from `tasks` + `job_runs`.
- Starting a run drives the existing `process_once` drain loop (same path as `pcn run-all`); `drain_process_queue` is shared by CLI and web.
- Stop is cooperative: `drain_process_queue` honors `should_stop` between work units (proven by `test_drain_stops_when_should_stop_true`).
- Import defaults to **enqueue-only and returns immediately** (`{imported_files, queued: true}`); it never blocks on a drain. `wait=true` runs the mock pipeline end to end synchronously; the UI drives real runs via `POST /api/pipeline/run`.
- SSE is served at `GET /api/events` (its own router), matching the contract and the frontend `EventSource`; `/api/pipeline/events` returns 404.
- `POST /api/pipeline/tasks/{id}/retry` returns `{task_id, status}` on success and 404 (from `retry_task`'s `ValueError`) for an unknown task.
- `require_accepted_transcripts` defaults `False` (autonomous preserved); with it on, only `accepted` segments feed `summarize_session`/`generate_daily_context`. The gate SQL exists in exactly one function.
- Speaker→person assignment, segment override, and person create/list go through the single extracted writer / one persons table; `test_speaker_review.py` still passes.
- Day → session navigation: `GET /api/transcripts/days` and `/days/{day}/sessions` (with `review_status`) back the by-day session picker.
- Segment audio plays via a domain helper; read-only LLM results are served from `summaries`/`memory_candidates` (404 when a session summary is missing).
- **The frontend closes the loop:** the App container imports a directory, starts/stops the worker, shows live SSE task status and the stage rail; `WorkspaceNav` navigates by day → session; the transcript panel accepts segments and overrides a segment's person; the speaker panel assigns speakers to persons (and creates persons); the LLM panel shows results read-only with an Obsidian pointer. Each is covered by a Vitest component/container test, and the full chain by `web/e2e/control-panel.spec.ts`.
- v1 web acceptance covers transcript + speaker/person; memory-candidate sign-off remains in Obsidian (documented as an intentional boundary).
- Backend suite, frontend tests, browser e2e, and ruff all pass.
- Obsidian remains the final knowledge surface; raw audio and transcripts stay local.

---

## Execution Notes / Guardrails

- Do not add a parallel orchestrator, run table, or second status enum.
- Do not bind to `0.0.0.0`; do not add cloud hosting.
- Do not hard-wire the acceptance gate; it must stay behind `require_accepted_transcripts` (default off).
- Do not duplicate the speaker-mapping SQL; both paths call the extracted writer.
- Do not add an HTTP memory-candidate mutation path in v1 (keep the Obsidian-markdown control surface). The `/api/llm/...` endpoints are read-only.
- Do not put SQL in `web/routes_*.py`; new queries live in domain modules (`transcript_review`, `transcription`, `llm_results`, `tasks`).
- Do not gate the metrics query in `llm_processing.py`.
- Keep `.tmp/` and `sample_data/` untracked.

---

## Self-Review

**Spec coverage**

- One-orchestrator rule: Tasks 2 (shared drain loop), 5 (web worker drives the same loop), 4 (observe `tasks`/`job_runs`). No `pipeline_runs` — asserted in `test_import_enqueues_vad_task_and_does_not_create_parallel_run_table`.
- Cooperative stop on the real loop: Task 2 (`should_stop`) + Task 5 (in-process event).
- Opt-in gate, autonomous default preserved: Tasks 6 (flag + single predicate) and 7 (both-mode tests; default off).
- One writer for speaker state; persons list/create: Task 9 (extraction + shared use; persons endpoints; regression on `test_speaker_review.py`).
- Day → session navigation API: Task 8 (`list_days` / `sessions_for_day` + routes).
- Read-only LLM surface: Task 11 (`llm_results.py` + `routes_llm.py`).
- SQL in domain modules: Tasks 6 (`transcript_review`), 8 (`list_days`/`sessions_for_day`), 10 (`transcription.segment_audio_path`), 11 (`llm_results`), 4 (reuses `process_status_rows`).
- Two-process safety: Task 1 (WAL + busy_timeout).
- **Frontend actually wired:** Task 12 (typed client + SSE hook), Task 13 (live App container: import/run/stop/status/rail/task list), Task 14 (transcript + speaker/person panels), Task 15 (read-only LLM panel), Task 16 (compose nav + panels into App), Task 17 (full-chain e2e). Closes the review gap where `App.tsx` was static and the panels were unreachable.
- Docs + design-doc reconciliation: Task 18.

**Placeholder scan**

- No `TBD`/`TODO`, no placeholder routes, no create-then-delete steps. `RetryTaskResult` (`task_id`, `status`) and `retry_task`'s `ValueError`-on-missing are pinned from source (`tasks.py:29-32`, `:211-234`). Remaining "match the real symbol" notes (`job_status_rows` kwarg, `audio_chunks` columns) are correctness guards against drift, verified against the schema in this plan.

**Type consistency**

- `drain_process_queue` / `DrainResult` fields used consistently in Tasks 2, 5, 16.
- Review status values (`pending_review`/`accepted`/`rejected`/`needs_fix`) match across `transcript_review.py`, the review API, and `web/src/api/types.ts`.
- `PipelineWorker` methods (`is_running`, `start`, `request_stop`, `drain_now`) referenced consistently in routes and status overview.
- `accepted_segments_clause(alias)` matches its call sites in `session_summaries.py` and `llm_processing.py`.
- Every API contract endpoint maps to an implementing task; every frontend `api` client method maps to a contract endpoint (`importDir`/`run`/`stop`/`retry` → pipeline; `days`/`sessionsForDay`/`session`/`reviewSegment`/`acceptRemaining` → transcripts; `persons`/`createPerson`/`assignPerson`/`overridePerson` → persons/speakers; `dailyLlm` → llm; `audioUrl` → audio).
