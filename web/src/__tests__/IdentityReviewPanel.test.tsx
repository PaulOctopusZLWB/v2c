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
