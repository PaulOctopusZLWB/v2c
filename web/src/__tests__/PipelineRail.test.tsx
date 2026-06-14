import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PipelineRail } from "../components/PipelineRail";

describe("PipelineRail", () => {
  it("renders the six Chinese stages and marks the active one live", () => {
    render(<PipelineRail activeStage="asr" />);
    for (const label of ["设备", "导入", "转写", "审核", "观点", "发布"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("转写").closest(".stage")?.className).toContain("active");
  });

  it("fires onSelect with the stage id when a stage button is clicked", async () => {
    const onSelect = vi.fn();
    render(<PipelineRail activeStage="asr" onSelect={onSelect} />);
    await userEvent.click(screen.getByText("转写"));
    expect(onSelect).toHaveBeenCalledWith("asr");
  });
});
