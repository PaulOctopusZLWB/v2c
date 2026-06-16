import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TranscriptReviewPanel } from "../features/transcript/TranscriptReviewPanel";

const session = {
  session_id: "ses_1",
  review_status: "pending_review" as const,
  segments: [{ segment_id: "seg_1", text: "你好", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "pending_review" as const, note: null }]
};

describe("TranscriptReviewPanel", () => {
  it("accepts a segment and overrides its person", async () => {
    const onReview = vi.fn();
    const onOverride = vi.fn();
    render(
      <TranscriptReviewPanel
        session={session}
        persons={[{ person_id: "per_paul", display_name: "Paul", person_type: "self", is_self: 1 }]}
        onReview={onReview}
        onOverride={onOverride}
        onPlay={() => undefined}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "接受" }));
    expect(onReview).toHaveBeenCalledWith("seg_1", "accepted");

    await userEvent.selectOptions(screen.getByLabelText("改人 seg_1"), "per_paul");
    expect(onOverride).toHaveBeenCalledWith("seg_1", "per_paul");
  });
});
