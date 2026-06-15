import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePipelineStatus } from "../hooks/usePipelineStatus";

describe("usePipelineStatus", () => {
  let summaryListener: ((event: { data: string }) => void) | null;

  beforeEach(() => {
    summaryListener = null;
    vi.stubGlobal(
      "EventSource",
      class {
        addEventListener(type: string, cb: (event: { data: string }) => void) {
          if (type === "status.summary") summaryListener = cb;
        }
        close() {}
      } as unknown as typeof EventSource
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it("exposes a summary fed by the status.summary SSE event", async () => {
    const { result } = renderHook(() => usePipelineStatus());
    // Starts with no summary yet.
    expect(result.current.summary).toBeNull();

    await waitFor(() => expect(summaryListener).not.toBeNull());
    act(() =>
      summaryListener!({
        data: JSON.stringify({
          status_counts: { succeeded: 1200, pending: 300, running: 1 },
          total: 1501,
          active_stage: "asr",
          current_target: "chk_9",
          import_progress: null,
          worker_running: true
        })
      })
    );

    expect(result.current.summary).not.toBeNull();
    expect(result.current.summary!.total).toBe(1501);
    expect(result.current.summary!.active_stage).toBe("asr");
    expect(result.current.summary!.status_counts.succeeded).toBe(1200);
    expect(result.current.worker_running).toBe(true);
  });
});
