import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TranscriptReviewPanel } from "../features/transcript/TranscriptReviewPanel";

// A 3-turn session: spk_1 (seg_1+seg_2), spk_2 (seg_3), spk_1 again (seg_4).
const session = {
  session_id: "ses_1",
  review_status: "pending_review" as const,
  segments: [
    { segment_id: "seg_1", text: "你好", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "pending_review" as const, note: null },
    { segment_id: "seg_2", text: "在的", speaker: "spk_1", start_ms: 1000, end_ms: 2000, absolute_start_at: "2026-06-13T09:33:01+08:00", absolute_end_at: "2026-06-13T09:33:02+08:00", review_status: "pending_review" as const, note: null },
    { segment_id: "seg_3", text: "我们开始吧", speaker: "spk_2", start_ms: 2000, end_ms: 3000, absolute_start_at: "2026-06-13T09:33:02+08:00", absolute_end_at: "2026-06-13T09:33:03+08:00", review_status: "pending_review" as const, note: null },
    { segment_id: "seg_4", text: "好的", speaker: "spk_1", start_ms: 3000, end_ms: 4000, absolute_start_at: "2026-06-13T09:33:03+08:00", absolute_end_at: "2026-06-13T09:33:04+08:00", review_status: "pending_review" as const, note: null }
  ]
};

function renderPanel(onBatchReview = vi.fn().mockResolvedValue(undefined)) {
  render(
    <TranscriptReviewPanel
      session={session}
      persons={[]}
      onBatchReview={onBatchReview}
      onAcceptSession={vi.fn()}
    />
  );
  return { onBatchReview };
}

/** The currently focused turn <article> (the one carrying the `focused` class). */
function focusedTurn(): HTMLElement {
  const el = document.querySelector(".turn.focused");
  if (!(el instanceof HTMLElement)) throw new Error("no focused turn");
  return el;
}

describe("keyboard-driven turn review", () => {
  it("starts with the first turn focused and moves the ring with j", () => {
    renderPanel();
    // First turn (spk_1) is focused initially.
    expect(focusedTurn()).toHaveTextContent("你好");

    fireEvent.keyDown(window, { key: "j" });
    // Focus moves to the 2nd turn (spk_2 · 我们开始吧).
    expect(focusedTurn()).toHaveTextContent("我们开始吧");
  });

  it("a accepts the FOCUSED turn's segment ids and advances focus", () => {
    const { onBatchReview } = renderPanel();
    // Move to the 2nd turn, then accept it.
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "a" });
    expect(onBatchReview).toHaveBeenCalledWith(["seg_3"], "accepted");
    // Focus auto-advances to the 3rd turn (spk_1 · 好的).
    expect(focusedTurn()).toHaveTextContent("好的");
  });

  it("? opens the shortcut sheet and Esc closes it", () => {
    renderPanel();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    // `?` == shift+/ per useHotkeys' eventToCombo contract.
    fireEvent.keyDown(window, { key: "/", shiftKey: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("ignores a/j while typing in an editable field (e.g. a <select>)", async () => {
    const { onBatchReview } = renderPanel();

    // useHotkeys ignores keystrokes from input/select/textarea/contenteditable targets.
    // Mount a <select>, focus it, and verify a/j there do nothing.
    const sel = document.createElement("select");
    document.body.appendChild(sel);
    sel.focus();

    await userEvent.keyboard("a");
    fireEvent.keyDown(sel, { key: "a" });
    fireEvent.keyDown(sel, { key: "j" });

    expect(onBatchReview).not.toHaveBeenCalled();
    // Focus ring did not move either.
    expect(focusedTurn()).toHaveTextContent("你好");

    sel.remove();
  });
});
