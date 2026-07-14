import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { IdentityReviewPanel } from "../features/speakers/IdentityReviewPanel";

function mockFetch() {
  const calls: Array<{ url: string; body?: unknown }> = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url === "/api/sessions/ses_1/identity-review") {
      return new Response(JSON.stringify({
        session_id: "ses_1",
        can_summarize: false,
        participants: [{ person_id: "per_a", display_name: "Alice", status: "present" }],
        candidates: [{ person_id: "per_b", display_name: "Bob", status: "suggested", segment_count: 2, safe_label: "未确认说话人_1", sample_text: "hello", segment_ids: ["seg_1"] }],
        new_person_candidates: [],
        negative_feedback_count: 0
      }), { status: 200 });
    }
    if (url === "/api/identity/not-person") return new Response(JSON.stringify({ recorded: 1 }), { status: 200 });
    if (url === "/api/sessions/ses_1/participants") return new Response(JSON.stringify({ person_id: "per_b", status: "present" }), { status: 200 });
    return new Response(JSON.stringify({}), { status: 200 });
  });
  return calls;
}

describe("IdentityReviewPanel", () => {
  it("shows participants and records not-person feedback", async () => {
    const calls = mockFetch();
    render(<IdentityReviewPanel sessionId="ses_1" onChanged={vi.fn()} onOpenClusters={vi.fn()} push={vi.fn()} />);

    expect(await screen.findByText("本场参与人")).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /不是 Bob/ }));

    await waitFor(() => expect(calls.some((c) => c.url === "/api/identity/not-person")).toBe(true));
  });

  it("re-identify button runs the identify pass and reports the stats", async () => {
    const calls = mockFetch();
    const push = vi.fn();
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url === "/api/sessions/ses_1/identify") {
        return new Response(JSON.stringify({
          session_id: "ses_1",
          excluded_absent: [],
          attributed: { assigned: 3, unassigned: 1, total: 4, per_person: {}, threshold: 0.5 },
          pruned: { pruned: { per_x: 2 }, total_segments: 4 },
          corrections_applied: 1,
          clusters: { clusters: 1, assigned: 4, unassigned: 0, scope_segments: 4 }
        }), { status: 200 });
      }
      if (url === "/api/sessions/ses_1/identity-review") {
        return new Response(JSON.stringify({
          session_id: "ses_1", can_summarize: false, participants: [], candidates: [],
          new_person_candidates: [], negative_feedback_count: 0
        }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });
    const onChanged = vi.fn();
    render(<IdentityReviewPanel sessionId="ses_1" onChanged={onChanged} push={push} />);

    await userEvent.click(await screen.findByRole("button", { name: "重新识别" }));

    await waitFor(() => expect(calls.some((c) => c.url === "/api/sessions/ses_1/identify")).toBe(true));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(push).toHaveBeenCalledWith("已重新识别本场", expect.stringMatching(/归属 3\/4.*剔除 2.*纠偏 1/));
  });

  it("absent verdict surfaces the cascade (cleared + re-identified) from the response", async () => {
    const push = vi.fn();
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/sessions/ses_1/identity-review") {
        return new Response(JSON.stringify({
          session_id: "ses_1",
          can_summarize: false,
          participants: [],
          candidates: [{ person_id: "per_b", display_name: "Bob", status: "suggested", segment_count: 2, safe_label: "未确认说话人_1", sample_text: "hello", segment_ids: ["seg_1"] }],
          new_person_candidates: [],
          negative_feedback_count: 0
        }), { status: 200 });
      }
      if (url === "/api/sessions/ses_1/participants") {
        return new Response(JSON.stringify({
          person_id: "per_b", display_name: "Bob", status: "absent",
          cascade: { cascade: "absent", cleared: 5 }, summary_enqueued: false
        }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });
    render(<IdentityReviewPanel sessionId="ses_1" onChanged={vi.fn()} push={push} />);

    await userEvent.click(await screen.findByRole("button", { name: "本场没出现" }));

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("已排除 Bob", expect.stringMatching(/清除 5 段推断归属/))
    );
  });

  it("turns excluded candidates into a next-step summary path", async () => {
    const openSummary = vi.fn();
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/sessions/ses_1/identity-review") {
        return new Response(JSON.stringify({
          session_id: "ses_1",
          can_summarize: true,
          participants: [
            { person_id: "per_a", display_name: "Alice", status: "present" },
            { person_id: "per_b", display_name: "Bob", status: "absent" }
          ],
          candidates: [{ person_id: "per_b", display_name: "Bob", status: "excluded", segment_count: 2, safe_label: "未确认说话人_1", sample_text: "hello", segment_ids: ["seg_1"] }],
          new_person_candidates: [],
          negative_feedback_count: 0
        }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });

    render(<IdentityReviewPanel sessionId="ses_1" onChanged={vi.fn()} onOpenSummary={openSummary} push={vi.fn()} />);

    expect(await screen.findByText("身份足够，去总结")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /不是 Bob/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "去总结" }));
    expect(openSummary).toHaveBeenCalledTimes(1);
  });
});
