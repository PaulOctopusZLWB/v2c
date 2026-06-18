import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VoiceprintWorkflowPanel } from "../features/speakers/VoiceprintWorkflowPanel";

describe("VoiceprintWorkflowPanel", () => {
  it("shows blocked projection when no scope is selected", () => {
    render(
      <VoiceprintWorkflowPanel
        selectedScopeCount={0}
        projection={{ status: "idle" }}
        selectedSegmentCount={0}
        hasKnownPeople={false}
        lastAutoAttributeCount={null}
        hasReviewTarget={false}
      />
    );
    expect(screen.getByText("声纹主路径")).toBeInTheDocument();
    expect(screen.getByText("先选择范围")).toBeInTheDocument();
  });

  it("shows projected point count", () => {
    render(
      <VoiceprintWorkflowPanel
        selectedScopeCount={2}
        projection={{ status: "ready", pointCount: 400, capped: false }}
        selectedSegmentCount={0}
        hasKnownPeople={true}
        lastAutoAttributeCount={null}
        hasReviewTarget={true}
      />
    );
    expect(screen.getByText("400 点")).toBeInTheDocument();
    expect(screen.getByText("在图上框选样本")).toBeInTheDocument();
  });
});
