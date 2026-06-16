import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SegmentRow } from "../features/transcript/SegmentRow";

const seg = { segment_id: "seg_1", text: "数据不出本机", speaker: "self", start_ms: 9000, end_ms: 12000, absolute_start_at: "2026-06-13T09:33:09.530000+08:00", absolute_end_at: "2026-06-13T09:33:12.000000+08:00", review_status: "pending_review" as const, note: null };

describe("SegmentRow", () => {
  it("renders Chinese status, a speaker chip, and fires review/override/play", async () => {
    const onReview = vi.fn(), onOverride = vi.fn(), onPlay = vi.fn();
    render(
      <SegmentRow segment={seg} persons={[{ person_id: "p1", display_name: "李雷", person_type: "contact", is_self: 0 }]}
        highlighted={false} onReview={onReview} onOverride={onOverride} onPlay={onPlay} />
    );
    expect(screen.getByText("待审")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "接受" }));
    expect(onReview).toHaveBeenCalledWith("seg_1", "accepted");
    await userEvent.click(screen.getByRole("button", { name: "播放" }));
    expect(onPlay).toHaveBeenCalledWith("seg_1");
  });

  it("shows the absolute wall-clock time, not the per-file offset", () => {
    render(<SegmentRow segment={seg} persons={[]} highlighted={false} onReview={vi.fn()} onOverride={vi.fn()} onPlay={vi.fn()} />);
    expect(screen.getByText("09:33:09")).toBeInTheDocument();   // from absolute_start_at
    expect(screen.queryByText("00:09")).not.toBeInTheDocument(); // NOT clock(start_ms=9000)
  });

  it("falls back to the per-file mm:ss when no absolute timestamp", () => {
    const legacy = { ...seg, absolute_start_at: null, absolute_end_at: null };
    render(<SegmentRow segment={legacy} persons={[]} highlighted={false} onReview={vi.fn()} onOverride={vi.fn()} onPlay={vi.fn()} />);
    expect(screen.getByText("00:09")).toBeInTheDocument();      // clock(9000)
  });

  it("reports playback failures", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("missing", { status: 404 }) as Response);
    const onPlaybackError = vi.fn();
    render(
      <SegmentRow
        segment={seg}
        persons={[]}
        highlighted={false}
        onReview={vi.fn()}
        onOverride={vi.fn()}
        onPlay={vi.fn()}
        onPlaybackError={onPlaybackError}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: /播放/ }));

    // playback runs fire-and-forget; wait for the rejection to propagate to the callback.
    await waitFor(() => expect(onPlaybackError).toHaveBeenCalledWith(expect.stringContaining("404")));
  });
});
