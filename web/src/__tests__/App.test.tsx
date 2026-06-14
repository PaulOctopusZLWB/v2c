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
