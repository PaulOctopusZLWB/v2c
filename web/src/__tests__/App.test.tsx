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
