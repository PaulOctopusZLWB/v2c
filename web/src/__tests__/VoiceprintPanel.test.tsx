import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceprintPanel } from "../features/speakers/VoiceprintPanel";
import type { Person } from "../api/types";

/** The component splits counts into child <span class="num"> nodes (codebase style), so a count
 *  like `已提取 1/3` spans several text nodes. Find the single leaf element whose own textContent
 *  matches and none of whose children do, so getByText returns exactly one node. */
function splitText(re: RegExp) {
  return (_: string, el: Element | null) =>
    !!el && re.test(el.textContent ?? "") && !Array.from(el.children).some((c) => re.test(c.textContent ?? ""));
}

const persons: Person[] = [
  { person_id: "per_lei", display_name: "李雷", person_type: "contact", is_self: 0 },
  { person_id: "per_han", display_name: "韩梅梅", person_type: "contact", is_self: 0 }
];

const segments = [
  { segment_id: "seg_1", text: "你好这是第一段", speaker: "spk_0", absolute_start_at: "2026-06-13T09:33:09+08:00", has_embedding: true },
  { segment_id: "seg_2", text: "这是第二段内容", speaker: "spk_1", absolute_start_at: "2026-06-13T09:34:00+08:00", has_embedding: true }
];

/** Mock fetch per URL; lets a test override `extra` for specific paths. */
function mockFetch(extra: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.split("?")[0];
    if (path === "/api/speakers/embedding-status")
      return new Response(JSON.stringify(extra[`embedding-status:${init?.method ?? "GET"}`] ?? extra["embedding-status"] ?? { total: 3, embedded: 1, pending: 2 }), { status: 200 });
    if (path === "/api/speakers/extract-embeddings")
      return new Response(JSON.stringify({ started: true }), { status: 200 });
    if (path === "/api/speakers/segments")
      return new Response(JSON.stringify({ segments }), { status: 200 });
    if (path === "/api/speakers/recluster")
      return new Response(JSON.stringify(extra["recluster"] ?? { assigned: 4, unassigned: 2, total: 6, per_person: { per_lei: 4 }, threshold: 0.5 }), { status: 200 });
    if (path === "/api/people/auto-attribute") {
      const status = (extra["auto-attribute:status"] as number | undefined) ?? 200;
      return new Response(JSON.stringify(extra["auto-attribute"] ?? { assigned: 5, unassigned: 1, total: 6, per_person: { per_lei: 5 }, threshold: 0.5 }), { status });
    }
    if (path === "/api/persons")
      return new Response(JSON.stringify({ persons }), { status: 200 });
    return new Response("{}", { status: 200 });
  });
}

describe("VoiceprintPanel", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch());
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders embedding coverage and POSTs extract-embeddings on click", async () => {
    render(<VoiceprintPanel sessionId="ses_1" persons={persons} />);

    expect(await screen.findByText(splitText(/已提取\s*1\s*\/\s*3/))).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /提取声纹/ }));

    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
    const extract = calls.find((c) => String(c[0]).startsWith("/api/speakers/extract-embeddings"));
    expect(extract).toBeTruthy();
    expect(extract![1]?.method).toBe("POST");
  });

  it("lists candidate segments for a session and enabling recluster after assigning a person", async () => {
    render(<VoiceprintPanel sessionId="ses_1" persons={persons} />);

    // segments listed
    expect(await screen.findByText("你好这是第一段")).toBeInTheDocument();
    expect(screen.getByText("这是第二段内容")).toBeInTheDocument();

    // recluster disabled while there are no anchors
    const reclusterBtn = screen.getByRole("button", { name: /重新归类/ });
    expect(reclusterBtn).toBeDisabled();

    // assign a person to the first segment
    const select = screen.getByLabelText("标注 seg_1");
    await userEvent.selectOptions(select, "per_lei");

    expect(reclusterBtn).toBeEnabled();
    expect(screen.getByText(/已标注\s*1/)).toBeInTheDocument();
  });

  it("POSTs recluster with anchors + threshold and renders the returned distribution", async () => {
    render(<VoiceprintPanel sessionId="ses_1" persons={persons} />);

    await screen.findByText("你好这是第一段");
    await userEvent.selectOptions(screen.getByLabelText("标注 seg_1"), "per_lei");

    await userEvent.click(screen.getByRole("button", { name: /重新归类/ }));

    await waitFor(() => expect(screen.getByText(splitText(/已归类\s*4\s*\/\s*6/))).toBeInTheDocument());
    expect(screen.getByText(splitText(/未定\s*2/))).toBeInTheDocument();

    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls;
    const recluster = calls.find((c) => String(c[0]).startsWith("/api/speakers/recluster"));
    expect(recluster).toBeTruthy();
    expect(recluster![1]?.method).toBe("POST");
    const body = JSON.parse(String(recluster![1]?.body));
    expect(body.anchors).toEqual({ seg_1: "per_lei" });
    expect(body.session_id).toBe("ses_1");
    expect(typeof body.threshold).toBe("number");

    // per-person breakdown resolves the person id to a display name (id "per_lei" -> "李雷")
    const breakdown = document.querySelector(".vp-breakdown") as HTMLElement;
    expect(breakdown).toBeTruthy();
    expect(breakdown.textContent).toContain("李雷");
  });

  it("gates the anchor section on a session being selected", async () => {
    render(<VoiceprintPanel day="2026-06-13" persons={persons} />);
    // no session -> a hint, no segment list / no fetch to /api/speakers/segments
    expect(await screen.findByText(/选择一个会话/)).toBeInTheDocument();
    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(calls.some((u) => u.startsWith("/api/speakers/segments"))).toBe(false);
  });

  it("auto-matches to existing people once extraction completes (toast + refresh)", async () => {
    // The poll re-reads embedding-status as pending 0 so the extraction pass resolves and the
    // auto-match fires exactly once. fireEvent (not userEvent) avoids fake-timer/click deadlocks.
    vi.stubGlobal("fetch", mockFetch({ "embedding-status": { total: 3, embedded: 3, pending: 0 } }));
    vi.useFakeTimers();
    const push = vi.fn();
    const onMatched = vi.fn();
    try {
      render(<VoiceprintPanel sessionId="ses_1" persons={persons} push={push} onMatched={onMatched} />);

      fireEvent.click(screen.getByRole("button", { name: /提取声纹/ }));
      // Flush the extract POST microtask, then advance past the 2s poll so the pass resolves and
      // triggers auto-match; flush its microtasks too.
      await act(async () => { await vi.advanceTimersByTimeAsync(2500); });

      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const auto = fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/people/auto-attribute"));
      expect(auto.length).toBe(1);
      expect(auto[0][1]?.method).toBe("POST");
      expect(JSON.parse(String(auto[0][1]?.body)).session_id).toBe("ses_1");

      expect(onMatched).toHaveBeenCalled();
      expect(push).toHaveBeenCalledWith(expect.stringMatching(/已自动匹配\s*5\s*\/\s*6.*未定\s*1/));
    } finally {
      vi.useRealTimers();
    }
  });

  it("manual 匹配到已有人物 button calls auto-attribute with the scope", async () => {
    const push = vi.fn();
    const onMatched = vi.fn();
    render(<VoiceprintPanel sessionId="ses_1" persons={persons} push={push} onMatched={onMatched} />);

    await userEvent.click(await screen.findByRole("button", { name: /匹配到已有人物/ }));

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const auto = fetchMock.mock.calls.find((c) => String(c[0]).startsWith("/api/people/auto-attribute"));
      expect(auto).toBeTruthy();
      expect(JSON.parse(String(auto![1]?.body)).session_id).toBe("ses_1");
    });
    await waitFor(() => expect(onMatched).toHaveBeenCalled());
  });

  it("shows a gentle hint (not an error) when nobody is labeled yet", async () => {
    vi.stubGlobal("fetch", mockFetch({ "auto-attribute:status": 400 }));
    const push = vi.fn();
    const onMatched = vi.fn();
    render(<VoiceprintPanel sessionId="ses_1" persons={persons} push={push} onMatched={onMatched} />);

    await userEvent.click(await screen.findByRole("button", { name: /匹配到已有人物/ }));

    await waitFor(() => expect(push).toHaveBeenCalled());
    // gentle hint, no "失败"/error wording, and the refresh isn't triggered
    const titles = push.mock.calls.map((c) => String(c[0]));
    expect(titles.some((t) => /先标注一些人物/.test(t))).toBe(true);
    expect(titles.some((t) => /失败/.test(t))).toBe(false);
    expect(onMatched).not.toHaveBeenCalled();
  });
});
