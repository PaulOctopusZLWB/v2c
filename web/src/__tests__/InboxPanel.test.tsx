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

function mockFetch(inbox = INBOX) {
  const calls: Array<{ url: string; body?: unknown }> = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.startsWith("/api/inbox")) return new Response(JSON.stringify(inbox), { status: 200 });
    if (url === "/api/sessions/ses_1/identity-review") return new Response(JSON.stringify(REVIEW), { status: 200 });
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
    expect(await screen.findByText("Bob")).toBeInTheDocument();
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
});
