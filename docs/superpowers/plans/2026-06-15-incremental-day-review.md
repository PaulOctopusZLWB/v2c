# Incremental Day Review at Scale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user start reviewing a day as soon as that day's audio is transcribed — instead of waiting for an entire ~1881-task import — and keep the control panel responsive at that scale.

**Architecture:** Two layers. (1) Backend scheduling: the type-priority claim loop currently starves day-finishing stages (`session_derive → … → obsidian_publish`) behind ALL transcription, so no day publishes until the whole import drains. Reorder so finishing stages preempt `asr`, and order the transcription backlog by recorded date, so days complete and become reviewable one-by-one in date order. Add lightweight status aggregates and a retry-all action. (2) Frontend: stop pushing the full 1881-row task array every second (drive progress from compact aggregate counts), refresh the day list live during a run with a per-day status badge, and make the task list virtualized + filterable with a retry-all button.

**Tech Stack:** Python/SQLite/FastAPI backend, React 18 + Vite frontend, pytest + Vitest. No new dependency (virtualization done with a manual visible-window slice).

---

## File Structure

- Modify `src/personal_context_node/process_runner.py`: `PROCESS_TASK_ORDER` reorder; date-ordered claim helper.
- Modify `src/personal_context_node/ingest.py` + `tasks.py`: set `priority` from recorded date at enqueue.
- Modify `src/personal_context_node/transcript_review.py`: add `day_status_rows()` (per-day processing/ready aggregate).
- Modify `src/personal_context_node/web/routes_status.py` + `routes_pipeline.py`: `status.summary` SSE event + `/api/transcripts/day-status` + `/api/pipeline/retry-failed`.
- Modify `web/src/`: `App.tsx`, `hooks/usePipelineStatus.ts`, `api/events.ts`, `api/client.ts`, `components/Progress.tsx`, `components/TaskList.tsx`, `features/workspace/WorkspaceNav.tsx`.
- Tests: `tests/test_process_runner.py`, `tests/test_tasks.py`, `tests/test_web_status_api.py`, `tests/test_web_pipeline_api.py`, `web/src/__tests__/*`.

---

## Phase A — Backend scheduling (incremental day completion)

## Task 1: Finishing stages preempt transcription

**Files:**
- Modify: `src/personal_context_node/process_runner.py:53-61`
- Modify: `tests/test_process_runner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_process_runner.py` (use the module's existing config/enqueue helpers): enqueue a backlog where one day's `asr` is complete (so its `session_derive` is claimable) AND another day still has pending `asr`. Assert the next `process_once` claims **session_derive**, not the other day's asr:

```python
def test_process_once_prefers_finishing_a_day_over_more_asr(tmp_path) -> None:
    config = _config(tmp_path)
    # day A: all chunks transcribed -> session_derive(A) is claimable
    # day B: a pending asr chunk still claimable
    _seed_day_ready_for_session_derive(config, day="2026-06-01")   # helper in this module
    _seed_pending_asr(config, day="2026-06-02")
    result = process_once(config=config, run_id="r", vad=MockVADAdapter(), asr=MockASRAdapter())
    assert result.task_type == "session_derive"   # finishes day A before transcribing day B
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_process_runner.py -q -k prefers_finishing_a_day`
Expected: FAIL — current order claims `asr` first.

- [ ] **Step 3: Reorder `PROCESS_TASK_ORDER`**

In `src/personal_context_node/process_runner.py`, change the constant so day-finishing stages outrank `asr` (keep `vad` first so chunks are produced; keep `archive` last):

```python
PROCESS_TASK_ORDER = (
    "vad",
    "session_derive",
    "summarize_session",
    "daily_generate",
    "obsidian_publish",
    "asr",
    "archive",
)
```

(The fan-in predicates already gate when each finishing stage becomes *claimable*, so correctness is unchanged; only the preference order changes — a ready day now publishes before the next asr chunk runs.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_process_runner.py -q`
Expected: PASS (this test + the existing DAG tests — the pipeline still advances vad→asr→…→publish).

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/process_runner.py tests/test_process_runner.py
git commit -m "feat(scheduler): finish & publish a ready day before transcribing more"
```

## Task 2: Order the transcription backlog by recorded date

**Files:**
- Modify: `src/personal_context_node/tasks.py` (enqueue priority)
- Modify: `src/personal_context_node/ingest.py`
- Modify: `tests/test_tasks.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tasks.py`: enqueue two `vad` tasks with explicit `priority` derived from date and assert `claim_next_task` returns the earlier-date one first (the claim already orders by `available_at, priority, created_at`):

```python
def test_claim_prefers_lower_priority_value(tmp_path) -> None:
    config = AppConfig(data_dir=tmp_path / "d", obsidian_vault=tmp_path / "v")
    enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="late", priority=20300)
    enqueue_task(config=config, task_type="vad", target_type="audio_file", target_id="early", priority=20260)
    claimed = claim_next_task(config=config, task_type="vad", run_id="r")
    assert claimed.target_id == "early"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_tasks.py -q -k prefers_lower_priority_value`
Expected: FAIL — `enqueue_task` has no `priority` parameter (it's always the schema default 100).

- [ ] **Step 3: Thread `priority` through enqueue, set it from recorded date in ingest**

In `src/personal_context_node/tasks.py`, give `enqueue_task` / `enqueue_task_in_conn` an optional `priority: int = 100` and include it in the INSERT column list/values.

In `src/personal_context_node/ingest.py`, when enqueuing the per-file `vad` task, compute a date-ordinal priority so earlier recorded days sort first, e.g.:

```python
from datetime import date
priority = (date.fromisoformat(recorded_at[:10]) - date(2000, 1, 1)).days
enqueue_task_in_conn(conn, task_type="vad", target_type="audio_file", target_id=audio_file_id, priority=priority)
```

In `process_runner.py:_enqueue_downstream_tasks_in_conn`, propagate the upstream task's `priority` to the tasks it mints (carry the date ordinal forward), so a day's asr/session/daily inherit its date priority and the whole day stays in date order.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tasks.py tests/test_process_runner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/tasks.py src/personal_context_node/ingest.py src/personal_context_node/process_runner.py tests/test_tasks.py
git commit -m "feat(scheduler): order backlog by recorded date via task priority"
```

## Task 3: Per-day status aggregate + retry-all endpoint

**Files:**
- Modify: `src/personal_context_node/transcript_review.py`
- Modify: `src/personal_context_node/tasks.py` (retry-all helper)
- Modify: `src/personal_context_node/web/routes_transcripts.py`, `routes_pipeline.py`, `routes_status.py`
- Modify: `tests/test_web_status_api.py`, `tests/test_web_pipeline_api.py`

- [ ] **Step 1: Write the failing API tests**

Add to `tests/test_web_status_api.py`: assert `GET /api/transcripts/day-status` returns each recorded day with a status of `processing` (still has non-terminal tasks) or `ready` (all its tasks terminal AND it has sessions). Add to `tests/test_web_pipeline_api.py`: assert `POST /api/pipeline/retry-failed` resets all `failed*` tasks to pending and returns the count.

```python
def test_retry_failed_resets_all_failed_tasks(client_with_failed_tasks) -> None:
    resp = client_with_failed_tasks.post("/api/pipeline/retry-failed")
    assert resp.status_code == 200
    assert resp.json()["retried"] >= 1
    # a subsequent status shows zero failed
    rows = client_with_failed_tasks.get("/api/status/tasks").json()["tasks"]
    assert not any(t["status"].startswith("failed") for t in rows)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_web_status_api.py tests/test_web_pipeline_api.py -q -k "day_status or retry_failed"`
Expected: FAIL — endpoints/functions missing.

- [ ] **Step 3: Implement aggregates + retry-all**

In `src/personal_context_node/transcript_review.py`, add `day_status_rows(*, config)` that joins `tasks → audio_files` on recorded day (`substr(recorded_at,1,10)`), and per day returns `{day, processing|ready, pending, failed, total}` (ready = no non-terminal tasks for that day's targets and a `sessions` row exists). Use one grouped query, not N+1.

In `src/personal_context_node/tasks.py`, add `retry_failed_tasks(*, config) -> int` that, in one `UPDATE`, resets every `status IN ('failed_retryable','failed_terminal')` task to pending with `retry_count=0, attempt_count=0, available_at=_now()` (same field set as `retry_task`).

In `routes_transcripts.py` add `GET /api/transcripts/day-status`; in `routes_pipeline.py` add `POST /api/pipeline/retry-failed` → `{"retried": retry_failed_tasks(config=...)}`; in `routes_status.py` add `GET /api/status/overview` returning `{status_counts, total, active_stage, current_target}` if not already present (reuse the existing Counter there).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_status_api.py tests/test_web_pipeline_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/personal_context_node/transcript_review.py src/personal_context_node/tasks.py src/personal_context_node/web/routes_transcripts.py src/personal_context_node/web/routes_pipeline.py src/personal_context_node/web/routes_status.py tests/test_web_status_api.py tests/test_web_pipeline_api.py
git commit -m "feat(web): per-day status aggregate + retry-all-failed endpoint"
```

## Task 4: SSE pushes a compact summary, not the full task array

**Files:**
- Modify: `src/personal_context_node/web/routes_pipeline.py:79-115`
- Modify: `tests/test_web_pipeline_api.py`

- [ ] **Step 1: Write the failing test**

Assert the SSE stream emits a `status.summary` event whose data is `{status_counts, total, active_stage, current_target, import_progress}` and that the change-signature is a compact digest (counts + `max(updated_at)`), not the 1881-tuple list.

- [ ] **Step 2: Run it to verify it fails** — Expected FAIL (only `status.snapshot` exists).

- [ ] **Step 3: Implement** — In `routes_pipeline.py`, compute the summary from `/api/status/overview`'s aggregate, emit `status.summary` on change, and build the change-signature from `(tuple(sorted(status_counts.items())), max_updated_at, import_progress)`. Keep `status.snapshot` (full rows) but emit it only on an explicit client request channel / or keep it for the on-demand `/api/status/tasks` GET, removing it from the per-second hot path.

- [ ] **Step 4: Run tests** — Expected PASS.

- [ ] **Step 5: Commit** — `feat(web): stream compact status.summary instead of full task rows`.

---

## Phase B — Frontend (responsive at 1881 tasks)

## Task 5: Drive progress from the summary + per-stage counts + ETA

**Files:**
- Modify: `web/src/api/events.ts`, `web/src/hooks/usePipelineStatus.ts`, `web/src/components/Progress.tsx`, `web/src/App.tsx`
- Modify: `web/src/__tests__/Progress.test.tsx`, `web/src/__tests__/App.test.tsx`

- [ ] **Step 1: Write the failing test** — `Progress` renders per-stage counts (`asr 1200/1500`) and an ETA when given a summary; `usePipelineStatus` exposes `summary`.
- [ ] **Step 2: Run it** — Expected FAIL.
- [ ] **Step 3: Implement** — `events.ts` subscribes to `status.summary`; `usePipelineStatus` returns `{summary}` (counts/total/active/current_target); `App` derives `done/total` and a stage breakdown from `summary`; `Progress` renders the breakdown + an ETA = `remaining * (sum(succeeded duration_ms)/succeeded_count)`. Keep the full task list fetched lazily (Task 7).
- [ ] **Step 4: Run** — `cd web && npm test -- Progress App` → PASS; `npm run build` clean.
- [ ] **Step 5: Commit** — `feat(web): per-stage progress + ETA from status summary`.

## Task 6: Live day list during a run + per-day status badge

**Files:**
- Modify: `web/src/api/client.ts`, `web/src/App.tsx`, `web/src/features/workspace/WorkspaceNav.tsx`
- Modify: `web/src/__tests__/WorkspaceNav.test.tsx`, `web/src/__tests__/App.test.tsx`

- [ ] **Step 1: Write the failing test** — `WorkspaceNav` renders a per-day badge (`处理中` / `可审`) from a `dayStatus` prop; `App` refreshes the day list during a run (not only on running→idle) and merges day-status.
- [ ] **Step 2: Run it** — Expected FAIL.
- [ ] **Step 3: Implement** — add `api.dayStatus()` (`GET /api/transcripts/day-status`); in `App`, fetch days + day-status together on the existing 5s poll (already gated on `bootstrappedRef`) AND opportunistically when the SSE summary shows a `session_derive`/`obsidian_publish` completion; pass `dayStatus` into `WorkspaceNav`, which shows each day with a `处理中`/`可审` badge next to its session count.
- [ ] **Step 4: Run** — `cd web && npm test -- WorkspaceNav App` → PASS; build clean.
- [ ] **Step 5: Commit** — `feat(web): live day list + per-day ready/processing badge`.

## Task 7: Virtualized, filterable task list + retry-all button

**Files:**
- Modify: `web/src/components/TaskList.tsx`, `web/src/api/client.ts`, `web/src/App.tsx`
- Modify: `web/src/__tests__/TaskList.test.tsx`

- [ ] **Step 1: Write the failing test** — Given 500 tasks, `TaskList` renders only a bounded window of rows (e.g. ≤ 60 DOM rows), exposes a `仅看失败` filter, and a `重试全部失败 (N)` button calls `onRetryAllFailed`.
- [ ] **Step 2: Run it** — Expected FAIL.
- [ ] **Step 3: Implement** —
  - Add a status filter (`all | failed | running`) and a manual windowing slice (render `tasks.slice(start, start+window)` driven by the `<details>` scroll position; no new dependency).
  - Add `api.retryFailed()` (`POST /api/pipeline/retry-failed`) and a `重试全部失败 (N)` button wired in `App` to `guard(async () => { await api.retryFailed(); await api.run(); })`.
  - Fetch the full task list lazily — only when the `<details>` opens (the SSE summary already feeds counts), via the existing `/api/status/tasks` GET.
- [ ] **Step 4: Run** — `cd web && npm test -- TaskList` → PASS; build clean.
- [ ] **Step 5: Commit** — `feat(web): virtualized filterable task list + retry-all-failed`.

## Task 8 (optional): "process this day next"

**Files:**
- Modify: `src/personal_context_node/tasks.py`, `routes_pipeline.py`, `web/src/features/workspace/WorkspaceNav.tsx`, `web/src/api/client.ts`

- [ ] **Step 1:** Add `prioritize_day(*, config, day) -> int` that sets a low `priority` (e.g. `-1`) on every task whose target traces to `day` (vad/asr via audio_files recorded day, session/daily via date_key). Test it changes claim order so that day's next task wins.
- [ ] **Step 2:** Add `POST /api/pipeline/prioritize-day/{day}` and a per-day "优先处理" button in `WorkspaceNav`. Test + commit `feat(web): prioritize a specific day in the queue`.

---

## Final Verification

- [ ] `uv run pytest -q` → PASS
- [ ] `cd web && npm test && npm run build` → PASS, clean
- [ ] Manual: import `sample_data`, confirm the earliest day appears with `处理中` then flips to `可审` and becomes reviewable **while later days are still transcribing**, the progress shows per-stage counts + ETA, and the task panel stays responsive with a working `重试全部失败`.

## Self-Review

- **Spec coverage:** date-major completion (Task 1), date ordering (Task 2), aggregate + retry-all (Task 3), compact SSE (Task 4), per-stage progress/ETA (Task 5), live days + badge (Task 6), virtualized list + retry-all button (Task 7), prioritize-day (Task 8). ✓
- **Placeholders:** Phase-A code is concrete; Phase-B tasks specify exact files, props, endpoints, and behaviors with named functions — when implementing, fill the React snippets following the existing component patterns (the test in each task's Step 1 pins the contract). Backend Steps 1 reference existing test-module helpers by name (`_config`, `_seed_*`) — reuse them from that module.
- **Type consistency:** `enqueue_task(..., priority=)`, `day_status_rows`, `retry_failed_tasks`, `prioritize_day`, `api.dayStatus()`, `api.retryFailed()`, `status.summary` event, `summary` hook field are referenced consistently across backend and frontend tasks.
