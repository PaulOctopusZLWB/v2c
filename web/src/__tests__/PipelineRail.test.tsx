import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineRail } from "../components/PipelineRail";

describe("PipelineRail", () => {
  it("renders the six stages and marks the active one", () => {
    render(<PipelineRail activeStage="asr" />);
    for (const label of ["Device", "Import", "ASR", "Transcript Review", "LLM", "Publish"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("ASR").className).toContain("active");
  });
});
