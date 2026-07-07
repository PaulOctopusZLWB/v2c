import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryPanel } from "../features/memory/MemoryPanel";
import type { MemoryCandidates } from "../api/types";

const listing: MemoryCandidates = {
  did: "did:key:z6MkabcdefExample9fQ2",
  pending: 2,
  total: 3,
  candidates: [
    {
      candidate_id: "cand_pref",
      day: "2087-05-10",
      claim: "更喜欢晚上写代码。",
      candidate_claim: "更喜欢晚上写代码。",
      claim_type: "preference",
      confidence: 0.86,
      source_type: "llm_daily_context",
      status: "pending_review",
      memory_card_id: null,
      reviewed_at: null,
      evidence: [
        { evidence_id: "ev_1", source_type: "transcript_segment", segment_id: "seg_1", session_id: "ses_1", quote: "我一般晚上效率最高。", summary: null }
      ],
      created_at: "2087-05-10T10:00:00+08:00"
    },
    {
      candidate_id: "cand_fact",
      day: "2087-05-10",
      claim: "项目截止日期是 6 月 30 日。",
      candidate_claim: "项目截止日期是 6 月 30 日。",
      claim_type: "fact",
      confidence: 0.92,
      source_type: "llm_daily_context",
      status: "pending_review",
      memory_card_id: null,
      reviewed_at: null,
      evidence: [],
      created_at: "2087-05-10T09:00:00+08:00"
    },
    {
      candidate_id: "cand_done",
      day: "2087-05-09",
      claim: "音频必须本地处理。",
      candidate_claim: "音频必须本地处理。",
      claim_type: "requirement",
      confidence: 0.95,
      source_type: "llm_daily_context",
      status: "confirmed",
      memory_card_id: "mem_cardid1234567890",
      reviewed_at: "2087-05-09T20:00:00+08:00",
      evidence: [],
      created_at: "2087-05-09T10:00:00+08:00"
    }
  ]
};

function stubFetch(overrides: Record<string, unknown> = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const path = String(url);
    if (path === "/api/memory/candidates")
      return new Response(JSON.stringify(overrides["candidates"] ?? listing), { status: 200 });
    if (path.endsWith("/confirm"))
      return new Response(JSON.stringify({ candidate_id: "cand_pref", card_id: "mem_new1", event_type: "memory_card.created", signature: "8f3adeadbeefc21d", note_path: "/vault/40_Confirmed_Memory/2087-05-10.md" }), { status: 200 });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  }));
  return calls;
}

const noop = () => {};

describe("MemoryPanel (记忆确认)", () => {
  beforeEach(() => stubFetch());
  afterEach(() => vi.unstubAllGlobals());

  it("renders the header (计数 + Ed25519 did 胶囊), cards with type pills and evidence rows", async () => {
    stubFetch();
    render(<MemoryPanel push={noop} />);

    expect(await screen.findByText("待确认记忆")).toBeInTheDocument();
    expect(screen.getByText(/Ed25519 ·/)).toBeInTheDocument();
    expect(screen.getByText("2 / 3")).toBeInTheDocument();

    expect(screen.getByText("偏好")).toBeInTheDocument();
    expect(screen.getByText("事实")).toBeInTheDocument();
    expect(screen.getByText(/「我一般晚上效率最高。」/)).toBeInTheDocument();
    // 已确认的卡带角标 + 回执行。
    expect(screen.getByText("✓ 已确认")).toBeInTheDocument();
    expect(screen.getByText(/已写回 40_Confirmed_Memory/)).toBeInTheDocument();
  });

  it("确认并签名 POSTs confirm and shows the signature receipt", async () => {
    const calls = stubFetch();
    render(<MemoryPanel push={noop} />);
    await screen.findByText("偏好");

    await userEvent.click(screen.getByRole("button", { name: /确认并签名/ }));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/memory/cand_pref/confirm" && c.init?.method === "POST")).toBe(true)
    );
  });

  it("拒绝 → z 撤销 restores the candidate", async () => {
    const calls = stubFetch();
    const push = vi.fn();
    render(<MemoryPanel push={push} />);
    await screen.findByText("偏好");

    await userEvent.click(screen.getByRole("button", { name: /^拒绝/ }));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/memory/cand_pref/reject" && c.init?.method === "POST")).toBe(true)
    );

    await userEvent.keyboard("z");
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/memory/cand_pref/restore" && c.init?.method === "POST")).toBe(true)
    );
  });

  it("z with nothing to undo explains that signed confirms are final", async () => {
    stubFetch();
    const push = vi.fn();
    render(<MemoryPanel push={push} />);
    await screen.findByText("偏好");
    await userEvent.keyboard("z");
    expect(push).toHaveBeenCalledWith("没有可撤销的操作", expect.stringMatching(/不可撤销/));
  });

  it("evidence 播放/跳到转写 buttons appear only for transcript evidence", async () => {
    stubFetch();
    const onJump = vi.fn();
    render(<MemoryPanel push={noop} onJumpToSegment={onJump} />);
    await screen.findByText("偏好");

    const evidence = screen.getByText(/我一般晚上效率最高/).closest(".memory-evidence") as HTMLElement;
    await userEvent.click(within(evidence).getByRole("button", { name: "跳到转写" }));
    expect(onJump).toHaveBeenCalledWith("seg_1", "ses_1");
  });

  it("shows the empty state when there are no candidates", async () => {
    stubFetch({ candidates: { did: "did:key:z6Mk", pending: 0, total: 0, candidates: [] } });
    render(<MemoryPanel push={noop} />);
    expect(await screen.findByText(/没有待确认的记忆候选/)).toBeInTheDocument();
  });
});
