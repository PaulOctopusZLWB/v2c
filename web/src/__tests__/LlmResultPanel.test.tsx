import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LlmResultPanel } from "../features/llm/LlmResultPanel";

describe("LlmResultPanel", () => {
  it("renders daily summary and read-only candidates with an Obsidian pointer", () => {
    render(
      <LlmResultPanel
        result={{
          day: "2087-05-10",
          context: { content: { summary: "今天讨论了部署" }, model_name: "rule_based", updated_at: "2087-05-10T09:00:00+08:00" },
          memory_candidates: [
            { candidate_id: "cand_1", candidate_claim: "Paul 喜欢咖啡", edited_claim: null, claim_type: "preference", confidence: 0.9, status: "pending" }
          ]
        }}
      />
    );
    expect(screen.getByText("今天讨论了部署")).toBeInTheDocument();
    expect(screen.getByText("Paul 喜欢咖啡")).toBeInTheDocument();
    expect(screen.getByText(/Obsidian/)).toBeInTheDocument();
    // Read-only: no confirm/reject controls.
    expect(screen.queryByRole("button", { name: /confirm/i })).toBeNull();
  });
});
