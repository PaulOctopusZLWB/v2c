import { describe, expect, it } from "vitest";
import { buildVoiceprintWorkflow } from "../features/speakers/voiceprintWorkflow";

describe("buildVoiceprintWorkflow", () => {
  it("starts at 提取声纹 with the rest pending when there is no data", () => {
    const steps = buildVoiceprintWorkflow({ status: null });
    expect(steps.map((s) => [s.id, s.state])).toEqual([
      ["extract", "current"],
      ["cluster", "pending"],
      ["assign", "pending"],
      ["noise", "pending"],
      ["confirm", "pending"],
    ]);
  });

  it("marks extraction running while voiceprints are still being computed", () => {
    const steps = buildVoiceprintWorkflow({
      status: { total: 100, embedded: 40, clusters: 0, identified: 0, unidentified: 100 },
    });
    expect(steps.find((s) => s.id === "extract")?.state).toBe("running");
    expect(steps.find((s) => s.id === "extract")?.detail).toContain("40/100");
    expect(steps.find((s) => s.id === "cluster")?.state).toBe("current");
  });

  it("advances to assign once clusters exist", () => {
    const steps = buildVoiceprintWorkflow({
      status: { total: 100, embedded: 100, clusters: 6, identified: 50, unidentified: 50 },
    });
    expect(steps.find((s) => s.id === "extract")?.state).toBe("complete");
    expect(steps.find((s) => s.id === "cluster")?.state).toBe("complete");
    expect(steps.find((s) => s.id === "assign")?.state).toBe("current");
    expect(steps.find((s) => s.id === "confirm")?.detail).toContain("50");
  });

  it("completes every step when unidentified reaches 0", () => {
    const steps = buildVoiceprintWorkflow({
      status: { total: 100, embedded: 100, clusters: 6, identified: 100, unidentified: 0 },
    });
    expect(steps.every((s) => s.state === "complete")).toBe(true);
    expect(steps.find((s) => s.id === "confirm")?.detail).toBe("可进入汇总");
  });
});
