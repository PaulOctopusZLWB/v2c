import { describe, expect, it } from "vitest";
import { buildVoiceprintWorkflow } from "../features/speakers/voiceprintWorkflow";

describe("buildVoiceprintWorkflow", () => {
  it("blocks projection when no scope is selected", () => {
    const steps = buildVoiceprintWorkflow({
      selectedScopeCount: 0,
      projection: { status: "idle" },
      selectedSegmentCount: 0,
      hasKnownPeople: true,
      lastAutoAttributeCount: null,
      hasReviewTarget: false
    });
    expect(steps.map((s) => [s.id, s.state])).toEqual([
      ["scope", "current"],
      ["project", "blocked"],
      ["label", "pending"],
      ["identify", "pending"],
      ["verify", "pending"]
    ]);
  });

  it("marks projection as running while the map is loading", () => {
    const steps = buildVoiceprintWorkflow({
      selectedScopeCount: 1,
      projection: { status: "loading" },
      selectedSegmentCount: 0,
      hasKnownPeople: true,
      lastAutoAttributeCount: null,
      hasReviewTarget: false
    });
    expect(steps.find((s) => s.id === "scope")?.state).toBe("complete");
    expect(steps.find((s) => s.id === "project")?.state).toBe("running");
  });

  it("moves to labeling after projection is ready", () => {
    const steps = buildVoiceprintWorkflow({
      selectedScopeCount: 1,
      projection: { status: "ready", pointCount: 1200, capped: true },
      selectedSegmentCount: 0,
      hasKnownPeople: true,
      lastAutoAttributeCount: null,
      hasReviewTarget: false
    });
    expect(steps.find((s) => s.id === "project")?.detail).toContain("1200");
    expect(steps.find((s) => s.id === "label")?.state).toBe("current");
  });

  it("moves to identify when segments are selected", () => {
    const steps = buildVoiceprintWorkflow({
      selectedScopeCount: 1,
      projection: { status: "ready", pointCount: 20, capped: false },
      selectedSegmentCount: 4,
      hasKnownPeople: true,
      lastAutoAttributeCount: null,
      hasReviewTarget: false
    });
    expect(steps.find((s) => s.id === "label")?.state).toBe("complete");
    expect(steps.find((s) => s.id === "identify")?.state).toBe("current");
  });

  it("marks verification current after auto attribution", () => {
    const steps = buildVoiceprintWorkflow({
      selectedScopeCount: 1,
      projection: { status: "ready", pointCount: 20, capped: false },
      selectedSegmentCount: 0,
      hasKnownPeople: true,
      lastAutoAttributeCount: 32,
      hasReviewTarget: true
    });
    expect(steps.find((s) => s.id === "identify")?.state).toBe("complete");
    expect(steps.find((s) => s.id === "verify")?.state).toBe("current");
  });
});
