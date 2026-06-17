import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { api } from "../api/client";

/** Click a workspace tab by its Chinese label, awaiting the role="tab" element. */
async function gotoTab(label: string) {
  await userEvent.click(await screen.findByRole("tab", { name: label }));
}

describe("App container", () => {
  beforeEach(() => {
    // Each test starts on a known tab regardless of the previous hash (useTab is hash-backed).
    window.location.hash = "";
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

  it("imports a detected device then starts a run", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }), { status: 200 });
      if (url === "/api/health") return new Response(JSON.stringify({ require_accepted_transcripts: false }), { status: 200 });
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [] }), { status: 200 });
      if (url === "/api/devices")
        return new Response(
          JSON.stringify({ sources: [{ kind: "device", device_id: "dev_1", label: "录音器 A", root_path: "/Volumes/REC", audio_count: 3 }] }),
          { status: 200 }
        );
      if (url === "/api/pipeline/import") return new Response(JSON.stringify({ imported_files: 3, queued: true }), { status: 200 });
      if (url === "/api/pipeline/run") return new Response(JSON.stringify({ worker_running: true }), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    render(<App />);
    // The DevicePanel + 导入 button live on the 录入 tab now.
    await gotoTab("录入");
    await userEvent.click(await screen.findByRole("button", { name: "导入" }));

    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContain("/api/pipeline/import");
    expect(calls).toContain("/api/pipeline/run");
  });

  it("shows 运行中 when the worker is running", async () => {
    // Running state now derives from the compact SSE `status.summary`, not the lazily
    // fetched full task array — so drive it through the summary event.
    let summaryListener: ((event: { data: string }) => void) | null = null;
    vi.stubGlobal("EventSource", class {
      addEventListener(type: string, cb: (event: { data: string }) => void) {
        if (type === "status.summary") summaryListener = cb;
      }
      close() {}
    } as unknown as typeof EventSource);
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async () => new Response("{}", { status: 200 }));

    render(<App />);
    await waitFor(() => expect(summaryListener).not.toBeNull());
    act(() =>
      summaryListener!({
        data: JSON.stringify({
          status_counts: { running: 1, pending: 2 },
          total: 3,
          active_stage: "asr",
          current_target: "a1",
          import_progress: null,
          worker_running: true
        })
      })
    );
    expect(await screen.findAllByText("运行中")).not.toHaveLength(0);
  });

  it("shows per-stage breakdown, ETA, and task count from the summary without opening the task list", async () => {
    let summaryListener: ((event: { data: string }) => void) | null = null;
    vi.stubGlobal("EventSource", class {
      addEventListener(type: string, cb: (event: { data: string }) => void) {
        if (type === "status.summary") summaryListener = cb;
      }
      close() {}
    } as unknown as typeof EventSource);
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async () => new Response("{}", { status: 200 }));

    render(<App />);
    await waitFor(() => expect(summaryListener).not.toBeNull());
    act(() =>
      summaryListener!({
        data: JSON.stringify({
          status_counts: { running: 200, succeeded: 1500 },
          total: 1700,
          stage_counts: { asr: { done: 1200, total: 1500 }, summarize_session: { done: 0, total: 200 } },
          eta_seconds: 300,
          active_stage: "asr",
          current_target: "chk_1",
          import_progress: null,
          worker_running: true
        })
      })
    );

    // The TaskList panel is never opened, so the lazy task array stays empty — these must
    // come from the compact summary. The stage breakdown + ETA live in the always-visible header.
    expect(await screen.findByText("1200/1500")).toBeInTheDocument(); // asr stage breakdown
    expect(screen.getByText(/剩余约/)).toBeInTheDocument(); // ETA
    // RunInspector (with the task count) lives on the 录入 tab.
    await gotoTab("录入");
    expect(await screen.findByText("1700")).toBeInTheDocument(); // RunInspector count == summary total
  });

  it("derives the progress done count and failed count from summary.done_total / failed_total", async () => {
    // doneCount = summary.done_total ?? fallback, failedCount = summary.failed_total ?? fallback.
    // Feed values that DIFFER from the fallback so a regression (dropping the summary fields, or
    // flipping done_total<->failed_total) changes a rendered value. Fallback done would be
    // total - running = 1700 - 200 = 1500, so done_total=1234 only matches if the field is used.
    let summaryListener: ((event: { data: string }) => void) | null = null;
    vi.stubGlobal("EventSource", class {
      addEventListener(type: string, cb: (event: { data: string }) => void) {
        if (type === "status.summary") summaryListener = cb;
      }
      close() {}
    } as unknown as typeof EventSource);
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async () => new Response("{}", { status: 200 }));

    render(<App />);
    await waitFor(() => expect(summaryListener).not.toBeNull());
    act(() =>
      summaryListener!({
        data: JSON.stringify({
          status_counts: { running: 200, succeeded: 1500 },
          total: 1700,
          done_total: 1234,
          failed_total: 42,
          active_stage: "asr",
          current_target: "chk_1",
          import_progress: null,
          worker_running: true
        })
      })
    );

    // The progress bar lives in the always-visible header.
    const bar = await screen.findByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "1234"); // from done_total, not the 1500 fallback
    expect(bar).toHaveAttribute("aria-valuemax", "1700");
    // failed_total flows to the TaskList "重试全部失败 (N)" control on the 录入 tab.
    await gotoTab("录入");
    expect(await screen.findByRole("button", { name: /重试全部失败/ })).toHaveTextContent("42");
  });

  it("refreshes the task list after a retry (api.retry -> api.run -> refreshTasks)", async () => {
    // The retry handlers end with `await refreshTasks()` so the open panel re-syncs. Open the
    // panel, retry the failed row, and assert the retry + run calls fire AND a fresh GET
    // /api/status/tasks follows — deleting refreshTasks() (or reordering it) fails this.
    let summaryListener: ((event: { data: string }) => void) | null = null;
    vi.stubGlobal("EventSource", class {
      addEventListener(type: string, cb: (event: { data: string }) => void) {
        if (type === "status.summary") summaryListener = cb;
      }
      close() {}
    } as unknown as typeof EventSource);
    const failedTask = {
      task_id: "task_x", task_type: "asr", target_type: "audio_chunk", target_id: "chk_x",
      status: "failed_retryable", attempt_count: 2, last_error: "model busy", duration_ms: 1200
    };
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [failedTask] }), { status: 200 });
      return new Response("{}", { status: 200 });
    });
    const urls = () => (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    const statusTasksCount = () => urls().filter((u) => u === "/api/status/tasks").length;

    const { container } = render(<App />);
    await waitFor(() => expect(summaryListener).not.toBeNull());
    // A summary with a non-zero total makes the TaskList render (count = summaryTotal), so its
    // panel can be opened to lazily load the rows.
    act(() => summaryListener!({ data: JSON.stringify({ status_counts: { failed_retryable: 1 }, total: 1, active_stage: null, current_target: null, import_progress: null, worker_running: false }) }));

    // The TaskList lives on the 录入 tab.
    await gotoTab("录入");
    // Open the <details> panel -> onToggle(true) -> refreshTasks() loads the failed row.
    const details = container.querySelector("details.task-list") as HTMLDetailsElement;
    details.open = true;
    act(() => { details.dispatchEvent(new Event("toggle")); });
    await screen.findByText("model busy"); // the failed row is now loaded
    const beforeRetry = statusTasksCount();

    await userEvent.click(screen.getByRole("button", { name: /重试$/ }));

    await waitFor(() => {
      expect(urls()).toContain("/api/pipeline/tasks/task_x/retry");
      expect(urls()).toContain("/api/pipeline/run");
      expect(statusTasksCount()).toBeGreaterThan(beforeRetry); // refreshTasks ran after the retry
    });
  });

  it("shows an actionable backend error when bootstrap API calls fail", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(new Response("broken", { status: 500 }));

    render(<App />);

    expect(await screen.findByText(/后端或 API 不可用/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument(); // announced to screen readers
    expect(screen.getByRole("button", { name: /重试/ })).toBeInTheDocument();
  });

  it("does not poll while the bootstrap error screen is shown", async () => {
    vi.useFakeTimers();
    try {
      const daysSpy = vi.spyOn(api, "days");
      (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(new Response("broken", { status: 500 }));

      render(<App />);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); }); // bootstrap fails
      const afterBootstrap = daysSpy.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); // poll tick

      // The poll is gated on bootstrap success, so it must not fill the rail behind the error.
      expect(daysSpy.mock.calls.length).toBe(afterBootstrap);
    } finally {
      vi.useRealTimers();
    }
  });

  it("re-lists days on the poll interval as a backstop", async () => {
    vi.useFakeTimers();
    try {
      const daysSpy = vi.spyOn(api, "days");
      (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
        if (url === "/api/persons") return new Response(JSON.stringify({ persons: [] }));
        if (url === "/api/health") return new Response(JSON.stringify({ require_accepted_transcripts: false }));
        if (url === "/api/devices") return new Response(JSON.stringify({ sources: [] }));
        if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [] }));
        if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }));
        return new Response(JSON.stringify({}));
      });

      render(<App />);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); }); // flush bootstrap
      const afterBootstrap = daysSpy.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); // one poll interval

      expect(daysSpy.mock.calls.length).toBeGreaterThan(afterBootstrap);
    } finally {
      vi.useRealTimers();
    }
  });

  it("refreshes days when a run completes (running -> idle)", async () => {
    // Migrated from the removed per-tick `status.snapshot` (full task array) to the
    // compact `status.summary`. Intent preserved: a running -> idle transition must
    // re-list days without a manual refresh.
    let summaryListener: ((event: { data: string }) => void) | null = null;
    vi.stubGlobal("EventSource", class {
      addEventListener(type: string, cb: (event: { data: string }) => void) {
        if (type === "status.summary") summaryListener = cb;
      }
      close() {}
    } as unknown as typeof EventSource);

    let runFinished = false;
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === "/api/persons") return new Response(JSON.stringify({ persons: [] }));
      if (url === "/api/health") return new Response(JSON.stringify({ require_accepted_transcripts: false }));
      if (url === "/api/devices") return new Response(JSON.stringify({ sources: [] }));
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: runFinished ? [{ day: "2087-05-10", session_count: 1 }] : [] }));
      if (url === "/api/transcripts/day-status") return new Response(JSON.stringify({ days: [] }));
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }));
      return new Response(JSON.stringify({}));
    });

    render(<App />);
    await waitFor(() => expect(summaryListener).not.toBeNull());

    // A run starts (pipeline becomes "running")...
    act(() => summaryListener!({ data: JSON.stringify({ status_counts: { running: 1 }, total: 1, active_stage: "asr", current_target: "a1", import_progress: null, worker_running: true }) }));
    // ...then finishes (running -> idle), which must re-list days without a manual refresh.
    runFinished = true;
    act(() => summaryListener!({ data: JSON.stringify({ status_counts: { succeeded: 1 }, total: 1, active_stage: null, current_target: null, import_progress: null, worker_running: false }) }));

    expect(await screen.findByRole("button", { name: /2087-05-10/ })).toBeInTheDocument();
  });

  it("fetches the per-day status aggregate alongside the day list on the poll", async () => {
    vi.useFakeTimers();
    try {
      const dayStatusSpy = vi.spyOn(api, "dayStatus");
      (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
        if (url === "/api/persons") return new Response(JSON.stringify({ persons: [] }));
        if (url === "/api/health") return new Response(JSON.stringify({ require_accepted_transcripts: false }));
        if (url === "/api/devices") return new Response(JSON.stringify({ sources: [] }));
        if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [] }));
        if (url === "/api/transcripts/day-status") return new Response(JSON.stringify({ days: [] }));
        if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }));
        return new Response(JSON.stringify({}));
      });

      render(<App />);
      await act(async () => { await vi.advanceTimersByTimeAsync(0); }); // flush bootstrap
      const afterBootstrap = dayStatusSpy.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); }); // one poll interval

      // The live badge needs the aggregate refreshed alongside the day list during a run.
      expect(dayStatusSpy.mock.calls.length).toBeGreaterThan(afterBootstrap);
    } finally {
      vi.useRealTimers();
    }
  });

  it("navigates day -> session and batch-accepts a turn", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }), { status: 200 });
      if (url === "/api/persons") return new Response(JSON.stringify({ persons: [{ person_id: "per_paul", display_name: "Paul", person_type: "self", is_self: 1 }] }), { status: 200 });
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [{ day: "2087-05-10", session_count: 1 }] }), { status: 200 });
      if (url === "/api/transcripts/days/2087-05-10/sessions") return new Response(JSON.stringify({ day: "2087-05-10", sessions: [{ session_id: "ses_1", started_at: "", segment_count: 1, review_status: "pending_review" }] }), { status: 200 });
      if (url === "/api/llm/days/2087-05-10") return new Response(JSON.stringify({ day: "2087-05-10", context: null, memory_candidates: [] }), { status: 200 });
      if (url === "/api/transcripts/sessions/ses_1") return new Response(JSON.stringify({ session_id: "ses_1", review_status: "pending_review", segments: [{ segment_id: "seg_1", text: "你好", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "pending_review", note: null }] }), { status: 200 });
      if (url === "/api/transcripts/segments/batch-review") return new Response(JSON.stringify({ updated: 1 }), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /2087-05-10/ }));
    await userEvent.click(await screen.findByRole("button", { name: /ses_1/ }));
    await userEvent.click(await screen.findByRole("button", { name: "接受整段" }));

    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContain("/api/transcripts/sessions/ses_1");
    expect(calls).toContain("/api/transcripts/segments/batch-review");
  });

  it("optimistically flips a turn's accepted count and shows an Undo toast", async () => {
    // Hold the batch-review response open so we can prove the UI updates BEFORE the API
    // resolves (the optimistic local-state patch), not after a refetch.
    let releaseBatch: (() => void) | null = null;
    const batchGate = new Promise<void>((resolve) => { releaseBatch = resolve; });
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }), { status: 200 });
      if (url === "/api/persons") return new Response(JSON.stringify({ persons: [] }), { status: 200 });
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [{ day: "2087-05-10", session_count: 1 }] }), { status: 200 });
      if (url === "/api/transcripts/days/2087-05-10/sessions") return new Response(JSON.stringify({ day: "2087-05-10", sessions: [{ session_id: "ses_1", started_at: "", segment_count: 1, review_status: "pending_review" }] }), { status: 200 });
      if (url === "/api/llm/days/2087-05-10") return new Response(JSON.stringify({ day: "2087-05-10", context: null, memory_candidates: [] }), { status: 200 });
      if (url === "/api/transcripts/sessions/ses_1") return new Response(JSON.stringify({ session_id: "ses_1", review_status: "pending_review", segments: [{ segment_id: "seg_1", text: "你好", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "pending_review", note: null }] }), { status: 200 });
      if (url === "/api/transcripts/segments/batch-review") { await batchGate; return new Response(JSON.stringify({ updated: 1 }), { status: 200 }); }
      return new Response("{}", { status: 200 });
    });

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /2087-05-10/ }));
    await userEvent.click(await screen.findByRole("button", { name: /ses_1/ }));
    expect(await screen.findByText("0/1 已接受")).toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: "接受整段" }));

    // The batch-review fetch is still pending, yet the turn's accepted count already updated.
    expect(await screen.findByText("1/1 已接受")).toBeInTheDocument();

    // Once the API resolves, the Undo toast appears.
    releaseBatch!();
    expect(await screen.findByRole("button", { name: "撤销" })).toBeInTheDocument();
  });

  it("undo restores a batch-accepted turn back to pending (clearReview) and refetches", async () => {
    const calls: string[] = [];
    let accepted = false; // the session refetch reflects the latest server truth
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string, init?: RequestInit) => {
      calls.push(url);
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }), { status: 200 });
      if (url === "/api/persons") return new Response(JSON.stringify({ persons: [] }), { status: 200 });
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [{ day: "2087-05-10", session_count: 1 }] }), { status: 200 });
      if (url === "/api/transcripts/days/2087-05-10/sessions") return new Response(JSON.stringify({ day: "2087-05-10", sessions: [{ session_id: "ses_1", started_at: "", segment_count: 1, review_status: "pending_review" }] }), { status: 200 });
      if (url === "/api/llm/days/2087-05-10") return new Response(JSON.stringify({ day: "2087-05-10", context: null, memory_candidates: [] }), { status: 200 });
      if (url === "/api/transcripts/sessions/ses_1") return new Response(JSON.stringify({ session_id: "ses_1", review_status: accepted ? "accepted" : "pending_review", segments: [{ segment_id: "seg_1", text: "你好", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: accepted ? "accepted" : "pending_review", note: null }] }), { status: 200 });
      if (url === "/api/transcripts/segments/batch-review") { accepted = JSON.parse(String(init?.body)).status === "accepted"; return new Response(JSON.stringify({ updated: 1 }), { status: 200 }); }
      if (url === "/api/transcripts/segments/clear-review") { accepted = false; return new Response(JSON.stringify({ cleared: 1 }), { status: 200 }); }
      return new Response("{}", { status: 200 });
    });

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /2087-05-10/ }));
    await userEvent.click(await screen.findByRole("button", { name: /ses_1/ }));
    await userEvent.click(await screen.findByRole("button", { name: "接受整段" }));
    expect(await screen.findByText("1/1 已接受")).toBeInTheDocument();

    // Undo: the segment was pending before, so undo CLEARS the review (back to pending).
    await userEvent.click(await screen.findByRole("button", { name: "撤销" }));
    await waitFor(() => expect(calls).toContain("/api/transcripts/segments/clear-review"));
    expect(await screen.findByText("0/1 已接受")).toBeInTheDocument();
  });

  it("renders only the active tab and keeps the selected session across tab switches", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }), { status: 200 });
      if (url === "/api/persons") return new Response(JSON.stringify({ persons: [{ person_id: "per_paul", display_name: "Paul", person_type: "self", is_self: 1 }] }), { status: 200 });
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: [{ day: "2087-05-10", session_count: 1 }] }), { status: 200 });
      if (url === "/api/transcripts/days/2087-05-10/sessions") return new Response(JSON.stringify({ day: "2087-05-10", sessions: [{ session_id: "ses_1", started_at: "", segment_count: 1, review_status: "pending_review" }] }), { status: 200 });
      if (url === "/api/llm/days/2087-05-10") return new Response(JSON.stringify({ day: "2087-05-10", context: null, memory_candidates: [] }), { status: 200 });
      if (url === "/api/transcripts/sessions/ses_1") return new Response(JSON.stringify({ session_id: "ses_1", review_status: "pending_review", segments: [{ segment_id: "seg_1", text: "你好", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "pending_review", note: null }] }), { status: 200 });
      if (url.includes("/embeddings/status")) return new Response(JSON.stringify({ embedded: 0, total: 0, pending: 0 }), { status: 200 });
      if (url.includes("/segments")) return new Response(JSON.stringify({ segments: [] }), { status: 200 });
      if (url.includes("/speaker-clusters")) return new Response(JSON.stringify({ clusters: [] }), { status: 200 });
      return new Response("{}", { status: 200 });
    });

    const { container } = render(<App />);

    // Default tab is 审核 — pick a day + session; the transcript panel mounts.
    await userEvent.click(await screen.findByRole("button", { name: /2087-05-10/ }));
    await userEvent.click(await screen.findByRole("button", { name: /ses_1/ }));
    await screen.findByText("你好"); // transcript content rendered
    expect(container.querySelector("#panel-transcript")).toBeInTheDocument();

    // Switch to 声纹: the transcript panel unmounts (only the active tab renders), and the
    // VoiceprintPanel shows. The session stays selected (it's App-level state).
    await gotoTab("声纹");
    expect(await screen.findByText("声纹覆盖")).toBeInTheDocument();
    expect(container.querySelector("#panel-transcript")).not.toBeInTheDocument();

    // Switch back to 审核: the transcript reappears with the same session still selected — no
    // need to re-pick the day/session.
    await gotoTab("审核");
    expect(await screen.findByText("你好")).toBeInTheDocument();
    expect(container.querySelector("#panel-transcript")).toBeInTheDocument();
  });
});
