import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TurnBlock } from "../features/transcript/TurnBlock";
import type { Turn } from "../lib/turns";
import type { TranscriptSegment } from "../api/types";

function seg(id: string, text: string, start: string, end: string): TranscriptSegment {
  return {
    segment_id: id,
    text,
    speaker: "spk_1",
    start_ms: 0,
    end_ms: 1000,
    absolute_start_at: start,
    absolute_end_at: end,
    review_status: "pending_review",
    note: null,
    person_id: null,
    person_label: null
  };
}

const segs = [
  seg("seg_1", "数据不出本机", "2026-06-13T09:33:09+08:00", "2026-06-13T09:33:12+08:00"),
  seg("seg_2", "全部在本地处理", "2026-06-13T09:33:12+08:00", "2026-06-13T09:33:15+08:00")
];

const turn: Turn = {
  speaker: "spk_1",
  label: "spk_1",
  personId: null,
  segments: segs,
  segment_ids: ["seg_1", "seg_2"],
  start: segs[0].absolute_start_at,
  end: segs[1].absolute_end_at
};

describe("TurnBlock", () => {
  it("renders the speaker chip and every sentence in the turn paragraph", () => {
    render(<TurnBlock turn={turn} persons={[]} onBatchReview={vi.fn()} />);
    expect(screen.getByText("spk_1")).toBeInTheDocument();
    expect(screen.getByText("数据不出本机")).toBeInTheDocument();
    expect(screen.getByText("全部在本地处理")).toBeInTheDocument();
    expect(screen.getByText(/09:33:09/)).toBeInTheDocument(); // turn start wall clock
  });

  it("renders the attributed person name on its chip (not the spk label)", () => {
    const attributed: Turn = { ...turn, label: "韩文巧", personId: "per_han" };
    render(<TurnBlock turn={attributed} persons={[]} onBatchReview={vi.fn()} />);
    expect(screen.getByText("韩文巧")).toBeInTheDocument();
    expect(screen.queryByText("spk_1")).not.toBeInTheDocument();
    // An attributed chip is NOT marked unattributed.
    const chip = screen.getByText("韩文巧").closest(".chip") as HTMLElement;
    expect(chip.classList.contains("unattributed")).toBe(false);
  });

  it("renders an unattributed turn with the muted spk label + 未识别 hint", () => {
    render(<TurnBlock turn={turn} persons={[]} onBatchReview={vi.fn()} />);
    const chip = screen.getByText("spk_1").closest(".chip") as HTMLElement;
    expect(chip.classList.contains("unattributed")).toBe(true);
    expect(chip.textContent).toContain("未识别");
  });

  it("plays a sentence's own audio when its span is clicked", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("missing", { status: 404 }) as Response);
    const onPlaybackError = vi.fn();
    render(<TurnBlock turn={turn} persons={[]} onBatchReview={vi.fn()} onPlaybackError={onPlaybackError} />);

    await userEvent.click(screen.getByText("全部在本地处理"));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith("/api/audio/segments/seg_2"));
    await waitFor(() => expect(onPlaybackError).toHaveBeenCalledWith(expect.stringContaining("404")));
  });

  it("batch-reviews the whole turn as accepted", async () => {
    const onBatchReview = vi.fn().mockResolvedValue(undefined);
    // 操作行只在焦点卡上展开(设计稿),按钮名带 mono 快捷键角标。
    render(<TurnBlock turn={turn} persons={[]} onBatchReview={onBatchReview} focused />);
    await userEvent.click(screen.getByRole("button", { name: /^接受/ }));
    expect(onBatchReview).toHaveBeenCalledWith(["seg_1", "seg_2"], "accepted");
  });
});
