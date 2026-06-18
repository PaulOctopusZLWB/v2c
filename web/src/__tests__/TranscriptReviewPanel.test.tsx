import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TranscriptReviewPanel } from "../features/transcript/TranscriptReviewPanel";

const session = {
  session_id: "ses_1",
  review_status: "pending_review" as const,
  segments: [
    { segment_id: "seg_1", text: "你好", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null },
    { segment_id: "seg_2", text: "在的", speaker: "spk_1", start_ms: 1000, end_ms: 2000, absolute_start_at: "2026-06-13T09:33:01+08:00", absolute_end_at: "2026-06-13T09:33:02+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null },
    { segment_id: "seg_3", text: "我们开始吧", speaker: "spk_2", start_ms: 2000, end_ms: 3000, absolute_start_at: "2026-06-13T09:33:02+08:00", absolute_end_at: "2026-06-13T09:33:03+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null }
  ]
};

describe("TranscriptReviewPanel", () => {
  it("groups segments into turns and batch-accepts a whole turn", async () => {
    const onBatchReview = vi.fn().mockResolvedValue(undefined);
    render(
      <TranscriptReviewPanel
        session={session}
        persons={[{ person_id: "per_paul", display_name: "Paul", person_type: "self", is_self: 1 }]}
        onBatchReview={onBatchReview}
        onAcceptSession={vi.fn()}
      />
    );
    // First turn (spk_1) merges seg_1 + seg_2 into one paragraph; second turn (spk_2) is seg_3.
    expect(screen.getByText("你好")).toBeInTheDocument();
    expect(screen.getByText("在的")).toBeInTheDocument();
    expect(screen.getByText("我们开始吧")).toBeInTheDocument();

    // Accept the first turn -> both of its segment ids in one batch call.
    await userEvent.click(screen.getAllByRole("button", { name: "接受整段" })[0]);
    expect(onBatchReview).toHaveBeenCalledWith(["seg_1", "seg_2"], "accepted");
  });

  it("accepts all of one speaker's segments via a per-speaker control", async () => {
    const onBatchReview = vi.fn().mockResolvedValue(undefined);
    render(
      <TranscriptReviewPanel
        session={session}
        persons={[]}
        onBatchReview={onBatchReview}
        onAcceptSession={vi.fn()}
      />
    );
    // One "接受此人全部" control per distinct speaker (spk_1, spk_2).
    const perSpeaker = screen.getAllByRole("button", { name: /接受此人全部/ });
    expect(perSpeaker).toHaveLength(2);
    await userEvent.click(perSpeaker[0]); // spk_1 -> seg_1 + seg_2
    expect(onBatchReview).toHaveBeenCalledWith(["seg_1", "seg_2"], "accepted");
  });

  it("uses resolved person labels for bulk identity controls", async () => {
    const onBatchReview = vi.fn().mockResolvedValue(undefined);
    const attributed = {
      session_id: "ses_attr",
      review_status: "pending_review" as const,
      segments: [
        { segment_id: "seg_1", text: "我是 Paul", speaker: "spk_01", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "pending_review" as const, note: null, person_id: "per_paul", person_label: "Paul" },
        { segment_id: "seg_2", text: "继续说", speaker: "spk_02", start_ms: 1000, end_ms: 2000, absolute_start_at: "2026-06-13T09:33:01+08:00", absolute_end_at: "2026-06-13T09:33:02+08:00", review_status: "pending_review" as const, note: null, person_id: "per_paul", person_label: "Paul" },
        { segment_id: "seg_3", text: "未识别", speaker: "spk_03", start_ms: 2000, end_ms: 3000, absolute_start_at: "2026-06-13T09:33:02+08:00", absolute_end_at: "2026-06-13T09:33:03+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null }
      ]
    };
    render(
      <TranscriptReviewPanel
        session={attributed}
        persons={[]}
        onBatchReview={onBatchReview}
        onAcceptSession={vi.fn()}
      />
    );

    const paul = screen.getByRole("button", { name: /接受 Paul 全部/ });
    expect(paul).toHaveTextContent("spk_01");
    expect(paul).toHaveTextContent("spk_02");
    expect(screen.queryByRole("button", { name: /接受此人全部 · spk_01/ })).not.toBeInTheDocument();

    await userEvent.click(paul);
    expect(onBatchReview).toHaveBeenCalledWith(["seg_1", "seg_2"], "accepted");
  });

  it("shows a current-session voiceprint match action when provided", async () => {
    const onMatchCurrentSession = vi.fn().mockResolvedValue(undefined);
    render(
      <TranscriptReviewPanel
        session={session}
        persons={[]}
        onBatchReview={vi.fn()}
        onAcceptSession={vi.fn()}
        onMatchCurrentSession={onMatchCurrentSession}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: /匹配当前会话/ }));

    expect(onMatchCurrentSession).toHaveBeenCalledTimes(1);
  });

  it("accepts the whole session via accept-remaining", async () => {
    const onAcceptSession = vi.fn().mockResolvedValue(undefined);
    render(
      <TranscriptReviewPanel
        session={session}
        persons={[]}
        onBatchReview={vi.fn()}
        onAcceptSession={onAcceptSession}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "接受整场" }));
    expect(onAcceptSession).toHaveBeenCalled();
  });

  it("hides ≤2-char filler turns when 隐藏碎语 is on, keeping substantive ones", async () => {
    // A session with a substantive turn (spk_1) and a separate ≤2-char filler turn (spk_2: "呃").
    const withFiller = {
      session_id: "ses_2",
      review_status: "pending_review" as const,
      segments: [
        { segment_id: "seg_1", text: "我们今天讨论方案", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null },
        { segment_id: "seg_2", text: "呃", speaker: "spk_2", start_ms: 1000, end_ms: 2000, absolute_start_at: "2026-06-13T09:33:01+08:00", absolute_end_at: "2026-06-13T09:33:02+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null }
      ]
    };
    render(<TranscriptReviewPanel session={withFiller} persons={[]} onBatchReview={vi.fn()} onAcceptSession={vi.fn()} />);

    // Both turns render initially.
    expect(screen.getByText("我们今天讨论方案")).toBeInTheDocument();
    expect(screen.getByText("呃")).toBeInTheDocument();

    // Toggle "隐藏碎语" -> the filler turn disappears, the substantive one stays.
    await userEvent.click(screen.getByRole("checkbox", { name: /隐藏碎语/ }));
    expect(screen.getByText("我们今天讨论方案")).toBeInTheDocument();
    expect(screen.queryByText("呃")).not.toBeInTheDocument();
  });

  it("hides fully-accepted turns when 仅未审 is on", async () => {
    const mixed = {
      session_id: "ses_3",
      review_status: "pending_review" as const,
      segments: [
        { segment_id: "seg_1", text: "已经审过的一段", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "accepted" as const, note: null, person_id: null, person_label: null },
        { segment_id: "seg_2", text: "还没审的一段", speaker: "spk_2", start_ms: 1000, end_ms: 2000, absolute_start_at: "2026-06-13T09:33:01+08:00", absolute_end_at: "2026-06-13T09:33:02+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null }
      ]
    };
    render(<TranscriptReviewPanel session={mixed} persons={[]} onBatchReview={vi.fn()} onAcceptSession={vi.fn()} />);

    expect(screen.getByText("已经审过的一段")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /仅未审/ }));
    expect(screen.queryByText("已经审过的一段")).not.toBeInTheDocument();
    expect(screen.getByText("还没审的一段")).toBeInTheDocument();
  });
});
