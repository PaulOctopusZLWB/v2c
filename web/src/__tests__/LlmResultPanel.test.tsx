import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LlmResultPanel } from "../features/llm/LlmResultPanel";

describe("LlmResultPanel", () => {
  it("renders read-only viewpoints with an Obsidian pointer and emits highlight on click", async () => {
    const onHighlight = vi.fn();
    render(
      <LlmResultPanel
        result={{ day: "2087-05-10", context: { content: { summary: "讨论部署" }, model_name: "rule_based", updated_at: "" },
          memory_candidates: [{ candidate_id: "c1", candidate_claim: "Paul 倾向数据不出本机", edited_claim: null, claim_type: "preference", confidence: 0.82, status: "pending", evidence_segment_ids: ["seg_3"] }] }}
        onHighlightEvidence={onHighlight}
      />
    );
    expect(screen.getByText("讨论部署")).toBeInTheDocument();
    expect(screen.getByText(/Obsidian/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /确认/ })).toBeNull(); // read-only
    await userEvent.click(screen.getByText(/Paul 倾向数据不出本机/));
    expect(onHighlight).toHaveBeenCalledWith("c1");
  });

  it("renders memory candidates as keyboard-reachable buttons", async () => {
    const onHighlightEvidence = vi.fn();
    render(
      <LlmResultPanel
        result={{ day: "2087-05-10", context: null,
          memory_candidates: [{ candidate_id: "c1", candidate_claim: "继续完善本地上下文系统", edited_claim: null, claim_type: "task", confidence: 0.7, status: "pending", evidence_segment_ids: ["seg_1"] }] }}
        onHighlightEvidence={onHighlightEvidence}
      />
    );

    const candidate = screen.getByRole("button", { name: /继续完善本地上下文系统/ });
    candidate.focus();
    expect(candidate).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(onHighlightEvidence).toHaveBeenCalledWith("c1");
  });
});
