import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PipelineRail } from "../components/PipelineRail";

describe("PipelineRail", () => {
  it("renders the six Chinese stages and marks the active one live", () => {
    render(<PipelineRail activeStage="asr" />);
    for (const label of ["设备", "导入", "转写", "审核", "观点", "发布"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("转写").closest(".stage")?.className).toContain("active");
  });
});
