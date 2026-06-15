import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { api } from "../api/client";

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
    // come from the compact summary.
    expect(await screen.findByText("1200/1500")).toBeInTheDocument(); // asr stage breakdown
    expect(screen.getByText(/剩余约/)).toBeInTheDocument(); // ETA
    expect(screen.getByText("1700")).toBeInTheDocument(); // RunInspector count == summary total
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
    await userEvent.click(await screen.findByRole("button", { name: "接受" }));

    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0]);
    expect(calls).toContain("/api/transcripts/sessions/ses_1");
    expect(calls).toContain("/api/transcripts/segments/seg_1/review");
  });
});
