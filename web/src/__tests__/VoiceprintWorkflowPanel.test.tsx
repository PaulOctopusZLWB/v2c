import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VoiceprintWorkflowPanel } from "../features/speakers/VoiceprintWorkflowPanel";

describe("VoiceprintWorkflowPanel", () => {
  it("renders the identify-first pipeline steps", () => {
    render(<VoiceprintWorkflowPanel status={null} />);
    expect(screen.getByText("声纹主路径")).toBeInTheDocument();
    expect(screen.getByText("提取声纹")).toBeInTheDocument();
    expect(screen.getByText("自动聚类")).toBeInTheDocument();
    expect(screen.getByText("分配聚类")).toBeInTheDocument();
  });

  it("shows the unidentified gate counter and flips to ready at 0", () => {
    const { rerender } = render(
      <VoiceprintWorkflowPanel status={{ total: 100, embedded: 100, clusters: 5, identified: 70, unidentified: 30 }} />
    );
    // The gate badge AND the confirm step both surface the count.
    expect(screen.getAllByText(/未识别/).length).toBeGreaterThan(0);
    expect(screen.getByText("30")).toBeInTheDocument();

    rerender(
      <VoiceprintWorkflowPanel status={{ total: 100, embedded: 100, clusters: 5, identified: 100, unidentified: 0 }} />
    );
    expect(screen.getAllByText("可进入汇总").length).toBeGreaterThan(0);
  });
});
