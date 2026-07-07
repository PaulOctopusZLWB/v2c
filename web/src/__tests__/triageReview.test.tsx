import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TranscriptReviewPanel } from "../features/transcript/TranscriptReviewPanel";
import type { SessionTriage } from "../api/types";

/** 4 段 3 turn:seg_bad(可疑,低置信+建议说话人)/ seg_mid(manual)/ seg_hi1+seg_hi2(高置信同 turn)。 */
const session = {
  session_id: "ses_t",
  name: "周会 · 项目排期",
  review_status: "pending_review" as const,
  segments: [
    { segment_id: "seg_bad", text: "嗯那个鉴全的部分", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2026-06-13T09:33:00+08:00", absolute_end_at: "2026-06-13T09:33:01+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null },
    { segment_id: "seg_mid", text: "后端周五联调", speaker: "spk_2", start_ms: 1000, end_ms: 2000, absolute_start_at: "2026-06-13T09:33:01+08:00", absolute_end_at: "2026-06-13T09:33:02+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null },
    { segment_id: "seg_hi1", text: "排期没问题", speaker: "spk_3", start_ms: 2000, end_ms: 3000, absolute_start_at: "2026-06-13T09:33:02+08:00", absolute_end_at: "2026-06-13T09:33:03+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null },
    { segment_id: "seg_hi2", text: "那就这样定了", speaker: "spk_3", start_ms: 3000, end_ms: 4000, absolute_start_at: "2026-06-13T09:33:03+08:00", absolute_end_at: "2026-06-13T09:33:04+08:00", review_status: "pending_review" as const, note: null, person_id: null, person_label: null }
  ]
};

const triage: SessionTriage = {
  session_id: "ses_t",
  thresholds: { high: 0.92, low: 0.75 },
  summary: {
    total: 4,
    bins: { high: 2, suspect: 1, manual: 1 },
    pending_high: 2,
    pending_suspect: 1,
    pending_manual: 1,
    reasons: { low_confidence: 1 }
  },
  segments: [
    { segment_id: "seg_bad", bin: "suspect", reasons: [{ kind: "low_confidence", label: "置信 0.41" }, { kind: "speaker_doubt", label: "说话人存疑 → 可能是 李雷" }], confidence: 0.41, review_status: "pending_review", suggested_text: null, suggested_speaker: { person_id: "per_lei", person_label: "李雷" } },
    { segment_id: "seg_mid", bin: "manual", reasons: [], confidence: 0.85, review_status: "pending_review", suggested_text: null, suggested_speaker: null },
    { segment_id: "seg_hi1", bin: "high", reasons: [], confidence: 0.97, review_status: "pending_review", suggested_text: null, suggested_speaker: null },
    { segment_id: "seg_hi2", bin: "high", reasons: [], confidence: 0.95, review_status: "pending_review", suggested_text: null, suggested_speaker: null }
  ]
};

function stubFetch() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (String(url) === "/api/sessions/ses_t/triage") return new Response(JSON.stringify(triage), { status: 200 });
    return new Response("{}", { status: 200 });
  }));
}

const baseProps = {
  session,
  persons: [],
  onBatchReview: vi.fn().mockResolvedValue(undefined),
  onAcceptSession: vi.fn()
};

describe("TranscriptReviewPanel — AI 预审", () => {
  beforeEach(stubFetch);
  afterEach(() => vi.unstubAllGlobals());

  it("shows the triage banner with live high/suspect counts and batch-accepts high on click", async () => {
    const onBatchReview = vi.fn().mockResolvedValue(undefined);
    render(<TranscriptReviewPanel {...baseProps} onBatchReview={onBatchReview} />);

    const banner = await screen.findByText(/AI 预审完成/);
    expect(banner.parentElement!.textContent).toMatch(/2.*段高置信建议直接接受/);
    expect(banner.parentElement!.textContent).toMatch(/1.*段可疑已前置/);

    await userEvent.click(screen.getByRole("button", { name: /接受 2 段高置信/ }));
    expect(onBatchReview).toHaveBeenCalledWith(["seg_hi1", "seg_hi2"], "accepted");
  });

  it("orders suspect turns first, shows reason pills, and collapses high-confidence turns", async () => {
    render(<TranscriptReviewPanel {...baseProps} />);
    await screen.findByText(/AI 预审完成/);

    // 可疑原因胶囊在卡头。
    expect(screen.getByText("置信 0.41")).toBeInTheDocument();
    expect(screen.getByText("说话人存疑 → 可能是 李雷")).toBeInTheDocument();

    // 高置信 turn 折叠:正文不可见,折叠行显示计数。
    expect(screen.queryByText("排期没问题")).not.toBeInTheDocument();
    const collapsed = screen.getByRole("button", { name: /已折叠/ });
    expect(collapsed.textContent).toMatch(/1.*段高置信/); // 1 个 turn(2 段)折叠

    // 展开后可复核。
    await userEvent.click(collapsed);
    expect(screen.getByText("排期没问题")).toBeInTheDocument();

    // 可疑 turn 排在 manual 之前。
    const texts = Array.from(document.querySelectorAll(".turn .turn-text")).map((el) => el.textContent);
    expect(texts[0]).toMatch(/鉴全/);
    expect(texts[1]).toMatch(/联调/);
  });

  it("采纳建议说话人 (e / button) hands segment ids + person to onAdoptSpeaker", async () => {
    const onAdoptSpeaker = vi.fn().mockResolvedValue(undefined);
    render(<TranscriptReviewPanel {...baseProps} onAdoptSpeaker={onAdoptSpeaker} />);
    await screen.findByText(/AI 预审完成/);

    // 焦点在首个(可疑)turn 上,操作行含「采纳 → 李雷 e」。
    await userEvent.click(screen.getByRole("button", { name: /采纳 → 李雷/ }));
    expect(onAdoptSpeaker).toHaveBeenCalledWith(["seg_bad"], "per_lei");
  });

  it("shows the completion card when nothing is pending and 归档 navigates away", async () => {
    const done = {
      ...session,
      segments: session.segments.map((s) => ({ ...s, review_status: "accepted" as const }))
    };
    const onArchive = vi.fn();
    render(<TranscriptReviewPanel {...baseProps} session={done} onArchive={onArchive} />);
    expect(await screen.findByText(/本场审核完成/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "接受整场并归档" }));
    expect(onArchive).toHaveBeenCalled();
  });

  it("renders the header progress (已审 x/N) and shortcut bar with 剩 n 段可疑", async () => {
    render(<TranscriptReviewPanel {...baseProps} />);
    await screen.findByText(/AI 预审完成/);
    expect(screen.getByRole("progressbar", { name: "审核进度" })).toHaveAttribute("aria-valuenow", "0");
    await waitFor(() => expect(screen.getByText(/剩 1 段可疑/)).toBeInTheDocument());
    // 头部会话名与 mono 元信息。
    expect(screen.getByRole("heading", { name: "周会 · 项目排期" })).toBeInTheDocument();
  });

  it("头部 ✎ 重命名走 promptText 对话框", async () => {
    const onRenameSession = vi.fn();
    const promptText = vi.fn(async () => "新名字");
    render(<TranscriptReviewPanel {...baseProps} onRenameSession={onRenameSession} promptText={promptText} />);
    await userEvent.click(await screen.findByRole("button", { name: "重命名会话" }));
    await waitFor(() => expect(onRenameSession).toHaveBeenCalledWith("新名字"));
    expect(promptText).toHaveBeenCalledWith(expect.objectContaining({ initial: "周会 · 项目排期" }));
  });

  it("degrades gracefully when triage fetch fails (no banner, plain review)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
    render(<TranscriptReviewPanel {...baseProps} />);
    // 所有 turn 直接可见(无折叠),无横幅。
    expect(await screen.findByText("排期没问题")).toBeInTheDocument();
    expect(screen.queryByText(/AI 预审完成/)).not.toBeInTheDocument();
  });
});

describe("keyboard ⇧A", () => {
  beforeEach(stubFetch);
  afterEach(() => vi.unstubAllGlobals());

  it("shift+a batch-accepts the pending high-confidence segments", async () => {
    const onBatchReview = vi.fn().mockResolvedValue(undefined);
    render(<TranscriptReviewPanel {...baseProps} onBatchReview={onBatchReview} />);
    await screen.findByText(/AI 预审完成/);
    await userEvent.keyboard("{Shift>}A{/Shift}");
    await waitFor(() => expect(onBatchReview).toHaveBeenCalledWith(["seg_hi1", "seg_hi2"], "accepted"));
  });
});

describe("collapsed count uses segment turns", () => {
  beforeEach(stubFetch);
  afterEach(() => vi.unstubAllGlobals());

  it("suspect reason pills disappear once the turn is decided", async () => {
    const decided = {
      ...session,
      segments: session.segments.map((s) =>
        s.segment_id === "seg_bad" ? { ...s, review_status: "rejected" as const } : s
      )
    };
    render(<TranscriptReviewPanel {...baseProps} session={decided} />);
    await screen.findByText(/AI 预审完成/);
    expect(screen.queryByText("置信 0.41")).not.toBeInTheDocument();
    expect(within(document.querySelector(".turn.is-rejected") as HTMLElement).getByText("✕ 已拒绝")).toBeInTheDocument();
  });
});
