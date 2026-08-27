import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { InboxSession } from "../api/types";
import { InboxPanel } from "../features/inbox/InboxPanel";

const INBOX: { pending: number; sessions: InboxSession[] } = {
  pending: 1,
  sessions: [
    {
      session_id: "ses_1",
      date_key: "2087-05-10",
      name: null,
      started_at: "2087-05-10T14:00:00+08:00",
      ended_at: "2087-05-10T14:47:00+08:00",
      segment_count: 12,
      attributed_count: 8,
      unidentified_count: 4,
      present: [],
      absent_count: 0,
      finalized: null
    },
    {
      session_id: "ses_0",
      date_key: "2087-05-10",
      name: "晨会",
      started_at: "2087-05-10T09:00:00+08:00",
      ended_at: "2087-05-10T09:30:00+08:00",
      segment_count: 6,
      attributed_count: 6,
      unidentified_count: 0,
      present: ["Alice"],
      absent_count: 0,
      finalized: { finalized_at: "now", export_md_path: "/data/exports/sessions/2087-05-10/ses_0.md" }
    }
  ]
};

const REVIEW = {
  session_id: "ses_1",
  can_summarize: true,
  can_finalize: true,
  finalized: null,
  participants: [],
  candidates: [
    {
      person_id: "per_b",
      display_name: "Bob",
      status: "suggested",
      safe_label: "未确认说话人_1",
      segment_count: 8,
      segment_ids: ["seg_1"],
      sample_text: "hello"
    }
  ],
  new_person_candidates: [
    { speaker: "vp_003", status: "unknown", safe_label: "未确认说话人_2", segment_count: 4, segment_ids: ["seg_2"], sample_text: "hi" }
  ],
  negative_feedback_count: 0
};

const TRANSCRIPT_SEGMENTS = [
  { segment_id: "seg_1", text: "先讨论本地部署", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2087-05-10T14:00:00+08:00", absolute_end_at: "2087-05-10T14:00:01+08:00", review_status: "accepted" as const, note: null, person_id: "per_b", person_label: "Bob" },
  { segment_id: "seg_2", text: "再确认导出路径", speaker: "spk_1", start_ms: 1000, end_ms: 2000, absolute_start_at: "2087-05-10T14:00:01+08:00", absolute_end_at: "2087-05-10T14:00:02+08:00", review_status: "accepted" as const, note: null, person_id: "per_b", person_label: "Bob" },
  { segment_id: "seg_3", text: "好的", speaker: "spk_2", start_ms: 2000, end_ms: 3000, absolute_start_at: "2087-05-10T14:00:02+08:00", absolute_end_at: "2087-05-10T14:00:03+08:00", review_status: "accepted" as const, note: null, person_id: null, person_label: null }
];

function mockFetch(inbox = INBOX, transcriptSegments = TRANSCRIPT_SEGMENTS) {
  const calls: Array<{ url: string; body?: unknown }> = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.startsWith("/api/inbox")) return new Response(JSON.stringify(inbox), { status: 200 });
    if (url === "/api/persons") {
      return new Response(JSON.stringify({
        persons: [{ person_id: "per_b", display_name: "Bob", person_type: "contact", is_self: 0 }]
      }), { status: 200 });
    }
    if (url === "/api/sessions/ses_1/identity-review") return new Response(JSON.stringify(REVIEW), { status: 200 });
    if (url === "/api/transcripts/sessions/ses_1") {
      return new Response(JSON.stringify({
        session_id: "ses_1",
        review_status: "accepted",
        segments: transcriptSegments
      }), { status: 200 });
    }
    if (url === "/api/sessions/ses_1/finalize") {
      return new Response(JSON.stringify({
        session_id: "ses_1", finalized_at: "now",
        export_md_path: "/data/exports/sessions/2087-05-10/ses_1.md",
        export_json_path: "/data/exports/sessions/2087-05-10/ses_1.json",
        present_count: 1, segment_count: 12, unidentified_voices: []
      }), { status: 200 });
    }
    if (url === "/api/sessions/ses_1/participants") {
      return new Response(JSON.stringify({ person_id: "per_b", display_name: "Bob", status: "present", cascade: { cascade: "none" } }), { status: 200 });
    }
    if (url === "/api/identity/confirm-candidate") {
      return new Response(JSON.stringify({ accepted: true, action: "noise", person_id: "per_noise", labeled: 4 }), { status: 200 });
    }
    if (url === "/api/inbox/finalize-ready") {
      return new Response(JSON.stringify({ finalized: [], skipped: [{ session_id: "ses_1" }] }), { status: 200 });
    }
    return new Response(JSON.stringify({}), { status: 200 });
  });
  return calls;
}

describe("InboxPanel", () => {
  it("opens the newest un-finalized session with attendance verdicts and finalize", async () => {
    const calls = mockFetch();
    const push = vi.fn();
    render(<InboxPanel push={push} />);

    // Newest un-finalized card auto-expands and shows its candidates.
    expect(await screen.findByText("Bob", { selector: "strong" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("收件箱统计")).getByText("1")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "待处理会话" })).toBeInTheDocument();
    // Machine labels are not part of the inbox vocabulary.
    expect(screen.queryByText(/vp_003/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "出现了" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/sessions/ses_1/participants")).toBe(true));

    await userEvent.click(screen.getByRole("button", { name: "定稿并导出" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/sessions/ses_1/finalize")).toBe(true));
    expect(push).toHaveBeenCalledWith("已定稿并导出", expect.stringContaining("ses_1.md"), "success");
  });

  it("keeps an all-finalized inbox useful by selecting the newest session", async () => {
    mockFetch({
      pending: 0,
      sessions: INBOX.sessions.map((session) => ({
        ...session,
        finalized: session.finalized ?? { finalized_at: "now", export_md_path: `/data/${session.session_id}.md` }
      }))
    });
    render(<InboxPanel push={vi.fn()} />);

    expect(await screen.findByLabelText("14:00 会话详情")).toBeInTheDocument();
    expect(screen.getByText("没有等待确认的会话")).toBeInTheDocument();
    const archive = screen.getByRole("region", { name: "已定稿会话" });
    expect(within(archive).getAllByRole("button")).toHaveLength(2);
    await userEvent.click(within(archive).getByRole("button", { name: /晨会/ }));
    expect(await screen.findByLabelText("晨会详情")).toBeInTheDocument();
  });

  it("unknown voices offer the workbench drill-down", async () => {
    mockFetch();
    const openWorkbench = vi.fn();
    render(<InboxPanel push={vi.fn()} onOpenWorkbench={openWorkbench} />);

    await userEvent.click(await screen.findByRole("button", { name: "去认人" }));
    expect(openWorkbench).toHaveBeenCalledWith("ses_1");
  });

  it("assigns an unknown voice role and can export all ready sessions", async () => {
    const calls = mockFetch();
    render(<InboxPanel push={vi.fn()} />);

    await userEvent.click(await screen.findByRole("button", { name: "噪音/多人" }));
    await waitFor(() => {
      expect(calls).toContainEqual(expect.objectContaining({
        url: "/api/identity/confirm-candidate",
        body: expect.objectContaining({ action: "noise", session_id: "ses_1", segment_ids: ["seg_2"] })
      }));
    });

    await userEvent.click(screen.getByRole("button", { name: "导出全部已就绪" }));
    await waitFor(() => expect(calls.some((call) => call.url === "/api/inbox/finalize-ready")).toBe(true));
  });

  it("turns a large transcript into searchable speaker-filtered reading blocks", async () => {
    mockFetch();
    render(<InboxPanel push={vi.fn()} />);

    await userEvent.click(await screen.findByText("浏览原文与录音"));
    expect(await screen.findByRole("searchbox", { name: "搜索会话原文" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bob 2" })).toBeInTheDocument();

    await userEvent.type(screen.getByRole("searchbox", { name: "搜索会话原文" }), "导出");
    // Keep the matching sentence's surrounding turn for context.
    expect(screen.getByText("先讨论本地部署")).toBeInTheDocument();
    expect(screen.getByText("导出").tagName).toBe("MARK");
    expect(screen.getByText("1–1 / 1 轮")).toBeInTheDocument();
  });

  it("bounds huge evidence payloads to 40 reading blocks per page", async () => {
    const longTranscript = Array.from({ length: 41 }, (_, i) => ({
      segment_id: `seg_long_${i}`,
      text: `证据片段 ${i + 1}`,
      speaker: `spk_${i % 2}`,
      start_ms: i * 1000,
      end_ms: (i + 1) * 1000,
      absolute_start_at: `2087-05-10T14:${String(i).padStart(2, "0")}:00+08:00`,
      absolute_end_at: `2087-05-10T14:${String(i).padStart(2, "0")}:01+08:00`,
      review_status: "accepted" as const,
      note: null,
      person_id: null,
      person_label: null
    }));
    mockFetch(INBOX, longTranscript);
    render(<InboxPanel push={vi.fn()} />);

    await userEvent.click(await screen.findByText("浏览原文与录音"));
    expect(await screen.findByText("证据片段 40")).toBeInTheDocument();
    expect(screen.queryByText("证据片段 41")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "下一组" }));
    expect(screen.getByText("证据片段 41")).toBeInTheDocument();
  });
});
