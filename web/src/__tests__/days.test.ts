import { describe, expect, it } from "vitest";
import { mergeDaysWithStatus } from "../lib/days";

describe("mergeDaysWithStatus", () => {
  it("keeps status-only days visible in descending day order", () => {
    const merged = mergeDaysWithStatus(
      [{ day: "2026-07-06", session_count: 2 }],
      [
        { day: "2026-07-07", session_count: 0, active_count: 0, total_count: 3, status: "empty" },
        { day: "2026-07-06", session_count: 2, active_count: 0, total_count: 1, status: "ready" }
      ]
    );

    expect(merged).toEqual([
      { day: "2026-07-07", session_count: 0 },
      { day: "2026-07-06", session_count: 2 }
    ]);
  });
});
