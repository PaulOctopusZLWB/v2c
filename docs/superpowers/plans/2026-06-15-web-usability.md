# Web Usability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the control panel operationally useful: API failures are visible, navigation refreshes after import/run, failed tasks explain themselves, controls are keyboard reachable, playback failures surface, and narrow screens remain usable.

**Architecture:** Keep `App.tsx` as the top-level coordinator and avoid adding a state-management library. Use existing hooks/components, add small local state helpers, and keep API calls in `web/src/api/client.ts`.

**Tech Stack:** React 18, Vite, Vitest, Testing Library, TypeScript, CSS.

---

## File Structure

- Modify `web/src/App.tsx`: bootstrap state, refresh days after run/import/SSE idle, retry-and-run.
- Modify `web/src/hooks/usePipelineStatus.ts`: keep status seeding behavior and route failures through `App` bootstrap state only if `statusTasks()` becomes part of `refreshBootstrap`.
- Modify `web/src/components/TaskList.tsx`: show last error, attempts, targets, retry button semantics.
- Modify `web/src/components/Toasts.tsx`: close button and alert/status semantics.
- Modify `web/src/features/llm/LlmResultPanel.tsx`: real buttons for candidates.
- Modify `web/src/hooks/useSegmentAudio.ts`: return playback errors.
- Modify `web/src/features/transcript/SegmentRow.tsx`: surface playback failure through callback.
- Modify `web/src/styles.css` and `web/src/theme.css`: responsive layout and wrapping.
- Modify `web/src/test-setup.ts`: stub media playback.
- Test `web/src/__tests__/App.test.tsx`.
- Add `web/src/__tests__/TaskList.test.tsx`.
- Test `web/src/__tests__/LlmResultPanel.test.tsx`.
- Test `web/src/__tests__/SegmentRow.test.tsx`.
- Keep `web/src/__tests__/Progress.test.tsx` unchanged unless a responsive CSS import test proves it must move.

## Task 1: Bootstrap API Failures Are Visible

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/__tests__/App.test.tsx`

- [ ] **Step 1: Write failing App bootstrap error test**

Add to `web/src/__tests__/App.test.tsx`:

```tsx
it("shows an actionable backend error when bootstrap API calls fail", async () => {
  (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(new Response("broken", { status: 500 }));

  render(<App />);

  expect(await screen.findByText(/后端或 API 不可用/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /重试/ })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test and verify failure**

```bash
cd web && npm test -- src/__tests__/App.test.tsx -t "bootstrap API calls fail"
```

Expected: FAIL because bootstrap errors are swallowed and empty state is shown.

- [ ] **Step 3: Implement bootstrap error state**

In `App.tsx`, add:

```tsx
const [bootstrapError, setBootstrapError] = useState<string | null>(null);
const [bootstrapped, setBootstrapped] = useState(false);

async function refreshBootstrap() {
  setBootstrapError(null);
  try {
    const [personsResult, healthResult, daysResult, devicesResult] = await Promise.all([
      api.persons(),
      api.health(),
      api.days(),
      api.devices()
    ]);
    setPersons(personsResult.persons ?? []);
    setHealth(healthResult);
    setDays(daysResult.days ?? []);
    setSources(devicesResult.sources ?? []);
    setBootstrapped(true);
  } catch (err) {
    setBootstrapError(err instanceof Error ? err.message : "API bootstrap failed");
  }
}
```

Use `void refreshBootstrap()` in the initial effect.

In `renderCenter()`, before first-run logic:

```tsx
if (bootstrapError && !bootstrapped) {
  return (
    <div className="empty error-state">
      <Icon name="run" className="empty-icon" />
      <h3>后端或 API 不可用</h3>
      <p>{bootstrapError}</p>
      <button className="primary" onClick={() => void refreshBootstrap()}>
        <Icon name="refresh" /> 重试
      </button>
    </div>
  );
}
```

Use `Icon name="run"` because it exists in `web/src/components/Icon.tsx`.

- [ ] **Step 4: Run App tests**

```bash
cd web && npm test -- src/__tests__/App.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/App.tsx web/src/__tests__/App.test.tsx
git commit -m "fix: show web bootstrap api errors"
```

## Task 2: Date Navigation Refreshes After Import and Run

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/__tests__/App.test.tsx`

- [ ] **Step 1: Write failing refresh-after-import test**

Add to `App.test.tsx`:

```tsx
it("refreshes days after import and run", async () => {
  const calls: string[] = [];
  (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (url === "/api/persons") return new Response(JSON.stringify({ persons: [] }));
    if (url === "/api/health") return new Response(JSON.stringify({ require_accepted_transcripts: false }));
    if (url === "/api/devices") return new Response(JSON.stringify({ sources: [{ kind: "known", device_id: "sample", label: "DJI Mic 3", root_path: "sample_data", audio_count: 1 }] }));
    if (url === "/api/transcripts/days") {
      const count = calls.filter((c) => c === "GET /api/transcripts/days").length;
      return new Response(JSON.stringify({ days: count > 1 ? [{ day: "2087-05-10", session_count: 1 }] : [] }));
    }
    if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }));
    if (url === "/api/pipeline/import") return new Response(JSON.stringify({ started: true, importing: true }));
    if (url === "/api/pipeline/run") return new Response(JSON.stringify({ worker_running: true }));
    return new Response(JSON.stringify({}));
  });

  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "导入" }));

  expect(await screen.findByRole("button", { name: /2087-05-10/ })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test and verify failure**

```bash
cd web && npm test -- src/__tests__/App.test.tsx -t "refreshes days after import and run"
```

Expected: FAIL because `handleImport` does not refresh days.

- [ ] **Step 3: Add refreshDays helper and call it**

In `App.tsx`:

```tsx
async function refreshDays() {
  const result = await api.days();
  setDays(result.days ?? []);
}
```

Update `handleImport`:

```tsx
await api.importDir(root);
await api.run();
await refreshDays();
```

- [ ] **Step 4: Refresh on running-to-idle transition**

Add:

```tsx
const wasPipelineRunning = useRef(false);
useEffect(() => {
  if (wasPipelineRunning.current && !pipelineRunning) {
    void refreshDays().catch((err) => push(t.error.title, err instanceof Error ? err.message : undefined));
  }
  wasPipelineRunning.current = pipelineRunning;
}, [pipelineRunning]);
```

Add `useRef` to React import.

- [ ] **Step 5: Run App tests**

```bash
cd web && npm test -- src/__tests__/App.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/App.tsx web/src/__tests__/App.test.tsx
git commit -m "fix: refresh web navigation after import"
```

## Task 3: Failed Task Diagnostics and Retry Run

**Files:**
- Modify: `web/src/components/TaskList.tsx`
- Modify: `web/src/App.tsx`
- Add: `web/src/__tests__/TaskList.test.tsx`

- [ ] **Step 1: Write failing TaskList diagnostics test**

Create `web/src/__tests__/TaskList.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TaskList } from "../components/TaskList";

describe("TaskList", () => {
  it("shows failed task diagnostics and retries", async () => {
    const onRetry = vi.fn();
    render(
      <TaskList
        tasks={[{
          task_id: "task_1",
          task_type: "asr",
          target_type: "audio_chunk",
          target_id: "chk_1",
          status: "failed_retryable",
          attempt_count: 2,
          last_error: "model busy",
          duration_ms: 1200
        }]}
        onRetry={onRetry}
      />
    );

    expect(screen.getByText("model busy")).toBeInTheDocument();
    expect(screen.getByText(/2/)).toBeInTheDocument();
    expect(screen.getByText("chk_1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /重试/ }));
    expect(onRetry).toHaveBeenCalledWith("task_1");
  });
});
```

- [ ] **Step 2: Run test and verify failure**

```bash
cd web && npm test -- src/__tests__/TaskList.test.tsx
```

Expected: FAIL because diagnostics are not rendered.

- [ ] **Step 3: Render diagnostics**

In `TaskList.tsx`, inside each row add:

```tsx
<span className="task-meta num">{task.target_id} · attempt {task.attempt_count}</span>
{task.last_error ? <span className="task-error">{task.last_error}</span> : null}
```

Make `.task-row` allow wrapping in CSS:

```css
.task-row { flex-wrap: wrap; align-items: flex-start; }
.task-meta, .task-error { width: 100%; font-size: 11px; }
.task-error { color: var(--err); word-break: break-word; }
```

- [ ] **Step 4: Retry starts worker**

In `App.tsx`, replace:

```tsx
<TaskList tasks={tasks} onRetry={guard((taskId: string) => api.retry(taskId))} />
```

with:

```tsx
<TaskList tasks={tasks} onRetry={guard(async (taskId: string) => { await api.retry(taskId); await api.run(); })} />
```

- [ ] **Step 5: Run tests**

```bash
cd web && npm test -- src/__tests__/TaskList.test.tsx src/__tests__/App.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/TaskList.tsx web/src/App.tsx web/src/styles.css web/src/__tests__/TaskList.test.tsx
git commit -m "fix: expose failed task diagnostics"
```

## Task 4: Keyboard-Reachable LLM Candidates and Toasts

**Files:**
- Modify: `web/src/features/llm/LlmResultPanel.tsx`
- Modify: `web/src/components/Toasts.tsx`
- Modify: `web/src/__tests__/LlmResultPanel.test.tsx`
- Add: `web/src/__tests__/Toasts.test.tsx`

- [ ] **Step 1: Write LLM keyboard test**

In `LlmResultPanel.test.tsx`, add:

```tsx
it("renders memory candidates as buttons", () => {
  const onHighlightEvidence = vi.fn();
  render(<LlmResultPanel result={result} onHighlightEvidence={onHighlightEvidence} />);

  const candidate = screen.getByRole("button", { name: /继续完善本地上下文系统/ });
  candidate.focus();
  fireEvent.keyDown(candidate, { key: "Enter" });
  expect(onHighlightEvidence).toHaveBeenCalled();
});
```

- [ ] **Step 2: Write Toast close button test**

Create `web/src/__tests__/Toasts.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Toasts } from "../components/Toasts";

describe("Toasts", () => {
  it("uses an explicit close button", async () => {
    const onDismiss = vi.fn();
    render(<Toasts toasts={[{ id: 1, title: "失败", message: "API failed" }]} onDismiss={onDismiss} />);

    expect(screen.getByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /关闭/ }));
    expect(onDismiss).toHaveBeenCalledWith(1);
  });
});
```

- [ ] **Step 3: Run tests and verify failure**

```bash
cd web && npm test -- src/__tests__/LlmResultPanel.test.tsx src/__tests__/Toasts.test.tsx
```

Expected: FAIL because candidates are divs and toasts have no close button.

- [ ] **Step 4: Convert candidates to buttons**

In `LlmResultPanel.tsx`, replace candidate wrapper with:

```tsx
<button className="viewpoint" type="button" key={c.candidate_id} onClick={() => onHighlightEvidence?.(c.candidate_id)}>
  <span className="claim">
    <Icon name="viewpoint" /> {c.edited_claim ?? c.candidate_claim}
  </span>
  <span className="meta num">
    {c.claim_type} · {Math.round(c.confidence * 100)}% · {statusZh(c.status)}
  </span>
</button>
```

- [ ] **Step 5: Add toast close button**

In `Toasts.tsx`, replace clickable toast div with:

```tsx
<div className="toast" key={toast.id} role="alert">
  <div className="t-title">{toast.title}</div>
  {toast.message ? <div className="dim">{toast.message}</div> : null}
  <button className="icon-btn ghost" type="button" aria-label="关闭通知" onClick={() => onDismiss(toast.id)}>
    ×
  </button>
</div>
```

- [ ] **Step 6: Run tests**

```bash
cd web && npm test -- src/__tests__/LlmResultPanel.test.tsx src/__tests__/Toasts.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add web/src/features/llm/LlmResultPanel.tsx web/src/components/Toasts.tsx web/src/__tests__/LlmResultPanel.test.tsx web/src/__tests__/Toasts.test.tsx
git commit -m "fix: make web alerts and candidates accessible"
```

## Task 5: Playback Errors Surface and Test Noise Is Removed

**Files:**
- Modify: `web/src/test-setup.ts`
- Modify: `web/src/hooks/useSegmentAudio.ts`
- Modify: `web/src/features/transcript/SegmentRow.tsx`
- Modify: `web/src/__tests__/SegmentRow.test.tsx`

- [ ] **Step 1: Stub media play in test setup**

In `web/src/test-setup.ts`, add:

```ts
Object.defineProperty(HTMLMediaElement.prototype, "play", {
  configurable: true,
  value: vi.fn().mockResolvedValue(undefined)
});
Object.defineProperty(HTMLMediaElement.prototype, "pause", {
  configurable: true,
  value: vi.fn()
});
```

Import `vi`:

```ts
import { vi } from "vitest";
```

- [ ] **Step 2: Run SegmentRow test and confirm stderr is gone**

```bash
cd web && npm test -- src/__tests__/SegmentRow.test.tsx
```

Expected: PASS without `HTMLMediaElement.prototype.play` stderr.

- [ ] **Step 3: Add playback failure callback test**

In `SegmentRow.test.tsx`, add:

```tsx
it("reports playback failures", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(new Response("missing", { status: 404 }) as Response);
  const onPlaybackError = vi.fn();
  render(
    <SegmentRow
      segment={seg}
      persons={[]}
      highlighted={false}
      onReview={vi.fn()}
      onOverride={vi.fn()}
      onPlay={vi.fn()}
      onPlaybackError={onPlaybackError}
    />
  );

  await userEvent.click(screen.getByRole("button", { name: /播放/ }));

  expect(onPlaybackError).toHaveBeenCalledWith(expect.stringContaining("404"));
});
```

The current `SegmentRow` props are `segment`, `persons`, `highlighted`, `isEvidence`, `onReview`, `onOverride`, and `onPlay`; this task adds only the optional `onPlaybackError` prop.

- [ ] **Step 4: Implement playback error path**

In `useSegmentAudio.ts`, check response status:

```ts
if (!response.ok) throw new Error(`audio request failed: ${response.status}`);
```

Have `play()` reject on fetch/decode/play failures instead of swallowing.

In `SegmentRow.tsx`, add prop:

```ts
onPlaybackError?: (message: string) => void;
```

In `handlePlay`, catch:

```ts
try {
  await audio.play(segment.segment_id);
} catch (err) {
  onPlaybackError?.(err instanceof Error ? err.message : "audio playback failed");
}
```

In `App.tsx`, pass:

```tsx
onPlaybackError={(message) => push("音频播放失败", message)}
```

- [ ] **Step 5: Run tests**

```bash
cd web && npm test -- src/__tests__/SegmentRow.test.tsx src/__tests__/App.test.tsx
```

Expected: PASS without media stderr.

- [ ] **Step 6: Commit**

```bash
git add web/src/test-setup.ts web/src/hooks/useSegmentAudio.ts web/src/features/transcript/SegmentRow.tsx web/src/App.tsx web/src/__tests__/SegmentRow.test.tsx
git commit -m "fix: surface audio playback failures"
```

## Task 6: Responsive Layout

**Files:**
- Modify: `web/src/styles.css`
- Modify: `web/src/theme.css`
- Add: `tests/test_web_styles.py`

- [ ] **Step 1: Add CSS presence test**

Create `tests/test_web_styles.py`:

```python
from pathlib import Path


def test_web_styles_define_responsive_breakpoints() -> None:
    css = Path("web/src/styles.css").read_text(encoding="utf-8")

    assert "@media (max-width: 1100px)" in css
    assert "grid-template-areas" in css
    assert "@media (max-width: 700px)" in css
```

- [ ] **Step 2: Run test and verify failure**

```bash
uv run pytest -q tests/test_web_styles.py
```

Expected: FAIL because breakpoints are absent.

- [ ] **Step 3: Add breakpoints**

In `web/src/styles.css`, append:

```css
@media (max-width: 1100px) {
  .workbench {
    grid-template-columns: 240px minmax(0, 1fr);
    grid-template-areas:
      "header header"
      "left center"
      "right right";
  }
  .rail-right {
    border-left: 0;
    border-top: 1px solid var(--border);
  }
  .workbench-header {
    flex-wrap: wrap;
  }
}

@media (max-width: 700px) {
  .workbench {
    grid-template-columns: 1fr;
    grid-template-areas:
      "header"
      "left"
      "center"
      "right";
  }
  .rail-left,
  .rail-right,
  .center-panel {
    border: 0;
    padding: 12px;
  }
  .segment-row {
    grid-template-columns: 1fr;
  }
  button {
    white-space: normal;
  }
}
```

- [ ] **Step 4: Run Web tests and build**

```bash
uv run pytest -q tests/test_web_styles.py
cd web && npm test
cd web && npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/styles.css web/src/theme.css tests/test_web_styles.py
git commit -m "fix: make web control panel responsive"
```

## Final Verification

- [ ] **Run Web suite and build**

```bash
cd web && npm test && npm run build
```

Expected: PASS and no jsdom media stderr.

- [ ] **Run live or deterministic e2e**

```bash
cd web && npm run e2e
```

Expected: PASS after operations startup plan provides deterministic e2e setup. If the live stack is unavailable, capture the exact blocker and run `uv run pytest -q tests/test_web_e2e.py` as the backend smoke.
