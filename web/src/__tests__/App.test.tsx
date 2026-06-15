import { act, render, screen, waitFor } from "@testing-library/react";
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
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === "/api/status/tasks")
        return new Response(
          JSON.stringify({
            tasks: [
              {
                task_id: "t1",
                task_type: "asr",
                target_type: "audio",
                target_id: "a1",
                status: "running",
                attempt_count: 1,
                last_error: null,
                duration_ms: null
              }
            ]
          }),
          { status: 200 }
        );
      return new Response("{}", { status: 200 });
    });

    render(<App />);
    expect(await screen.findAllByText("运行中")).not.toHaveLength(0);
  });

  it("shows an actionable backend error when bootstrap API calls fail", async () => {
    (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(new Response("broken", { status: 500 }));

    render(<App />);

    expect(await screen.findByText(/后端或 API 不可用/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重试/ })).toBeInTheDocument();
  });

  it("refreshes days after import and run", async () => {
    // Days are empty until an import runs; after import the new day must surface without
    // a manual refresh. Keyed off whether /api/pipeline/import was called (order-robust).
    let imported = false;
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === "/api/persons") return new Response(JSON.stringify({ persons: [] }));
      if (url === "/api/health") return new Response(JSON.stringify({ require_accepted_transcripts: false }));
      if (url === "/api/devices") return new Response(JSON.stringify({ sources: [{ kind: "known", device_id: "sample", label: "DJI Mic 3", root_path: "sample_data", audio_count: 1 }] }));
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: imported ? [{ day: "2087-05-10", session_count: 1 }] : [] }));
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }));
      if (url === "/api/pipeline/import") { imported = true; return new Response(JSON.stringify({ started: true, importing: true })); }
      if (url === "/api/pipeline/run") return new Response(JSON.stringify({ worker_running: true }));
      return new Response(JSON.stringify({}));
    });

    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "导入" }));

    expect(await screen.findByRole("button", { name: /2087-05-10/ })).toBeInTheDocument();
  });

  it("refreshes days when a run completes (running -> idle)", async () => {
    let statusListener: ((event: { data: string }) => void) | null = null;
    vi.stubGlobal("EventSource", class {
      addEventListener(type: string, cb: (event: { data: string }) => void) {
        if (type === "status.snapshot") statusListener = cb;
      }
      close() {}
    } as unknown as typeof EventSource);

    let runFinished = false;
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url === "/api/persons") return new Response(JSON.stringify({ persons: [] }));
      if (url === "/api/health") return new Response(JSON.stringify({ require_accepted_transcripts: false }));
      if (url === "/api/devices") return new Response(JSON.stringify({ sources: [] }));
      if (url === "/api/transcripts/days") return new Response(JSON.stringify({ days: runFinished ? [{ day: "2087-05-10", session_count: 1 }] : [] }));
      if (url === "/api/status/tasks") return new Response(JSON.stringify({ tasks: [] }));
      return new Response(JSON.stringify({}));
    });

    render(<App />);
    await waitFor(() => expect(statusListener).not.toBeNull());

    // A run starts (pipeline becomes "running")...
    act(() => statusListener!({ data: JSON.stringify({ tasks: [{ task_id: "t1", task_type: "asr", target_type: "audio", target_id: "a1", status: "running", attempt_count: 1, last_error: null, duration_ms: null }], worker_running: true }) }));
    // ...then finishes (running -> idle), which must re-list days without a manual refresh.
    runFinished = true;
    act(() => statusListener!({ data: JSON.stringify({ tasks: [], worker_running: false }) }));

    expect(await screen.findByRole("button", { name: /2087-05-10/ })).toBeInTheDocument();
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
