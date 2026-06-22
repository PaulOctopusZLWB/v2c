import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceprintMap } from "../features/viz/VoiceprintMap";
import type { PersonRow, ProjectionPoint, ProjectionRequest, ProjectionResult } from "../api/types";

/** Four points across two person/speaker keys (per_a x2, spk_1 x2) and two sessions. */
const points: ProjectionPoint[] = [
  { segment_id: "seg_1", x: 0.1, y: 0.2, speaker: "spk_0", person_id: "per_a", person_label: "李雷", text: "第一段", session_id: "ses_1" },
  { segment_id: "seg_2", x: 0.3, y: 0.4, speaker: "spk_0", person_id: "per_a", person_label: "李雷", text: "第二段", session_id: "ses_1" },
  { segment_id: "seg_3", x: 0.7, y: 0.6, speaker: "spk_1", person_id: null, person_label: null, text: "第三段", session_id: "ses_2" },
  { segment_id: "seg_4", x: 0.9, y: 0.8, speaker: "spk_1", person_id: null, person_label: null, text: "第四段", session_id: "ses_2" }
];

const projection: ProjectionResult = { points, method: "umap", n: 4 };

/** A request scoped to one session, default UMAP — the parent passes this in. */
const REQ: ProjectionRequest = { session_ids: ["ses_1"], days: [], method: "umap" };

/** A minimal 2D-context stub so canvas draw paths run without a real renderer (jsdom
 *  returns null from getContext). Every method is a no-op vi.fn(). */
function stubCanvas() {
  const ctx = new Proxy(
    {},
    {
      get: (_t, prop) => {
        if (prop === "canvas") return undefined;
        return vi.fn();
      },
      set: () => true
    }
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
}

/** Resolve POST /api/speakers/projection (+ emotion labels) with the given body; else `{}`. */
function mockFetch(body: unknown = projection, labels: Record<string, string> = {}) {
  return vi.fn(async (url: string) => {
    const path = String(url).split("?")[0];
    if (path === "/api/speakers/projection")
      return new Response(JSON.stringify(body), { status: 200 });
    if (path === "/api/emotions/labels")
      return new Response(JSON.stringify({ labels }), { status: 200 });
    return new Response("{}", { status: 200 });
  });
}

describe("VoiceprintMap", () => {
  beforeEach(() => {
    stubCanvas();
    vi.stubGlobal("fetch", mockFetch());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows the pick/empty state when request is null (no fetch)", () => {
    render(<VoiceprintMap request={null} />);
    expect(screen.getByText(/选择日期\/会话/)).toBeInTheDocument();
    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(calls.some((u) => u.startsWith("/api/speakers/projection"))).toBe(false);
  });

  it("shows a loading state, then a legend with the two keys and their counts", async () => {
    render(<VoiceprintMap request={REQ} />);

    // Loading state while the (slow) projection request is in flight.
    expect(screen.getByText(/正在投影声纹/)).toBeInTheDocument();

    // After it resolves, the legend lists both keys (person_label for labelled, speaker for not).
    const legend = await screen.findByRole("list", { name: /图例/ });
    expect(legend).toBeInTheDocument();
    const items = await screen.findAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(screen.getByText("李雷")).toBeInTheDocument(); // labelled key
    expect(screen.getByText("未识别")).toBeInTheDocument(); // unlabelled -> shared 未识别 bucket
    const counts = legend.querySelectorAll(".vmap-legend-count");
    expect(Array.from(counts).map((c) => c.textContent)).toEqual(["2", "2"]);
  });

  it("fetches via POST /api/speakers/projection with the request body", async () => {
    render(<VoiceprintMap request={REQ} />);
    await screen.findByRole("list", { name: /图例/ });

    const call = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(
      (c) => String(c[0]) === "/api/speakers/projection"
    );
    expect(call).toBeTruthy();
    expect(call![1]?.method).toBe("POST");
    expect(JSON.parse(call![1]!.body as string)).toMatchObject({ session_ids: ["ses_1"], method: "umap" });
  });

  it("shows the empty state when the projection returns no points (n===0)", async () => {
    vi.stubGlobal("fetch", mockFetch({ points: [], method: "umap", n: 0 }));
    render(<VoiceprintMap request={REQ} />);

    expect(await screen.findByText(/该范围还没有声纹/)).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: /图例/ })).not.toBeInTheDocument();
  });

  it("clicking a legend item focuses that cluster (dims the others)", async () => {
    render(<VoiceprintMap request={REQ} />);
    await screen.findByRole("list", { name: /图例/ });

    const leiItem = screen.getByText("李雷").closest(".vmap-legend-item") as HTMLElement;
    const spkItem = screen.getByText("未识别").closest(".vmap-legend-item") as HTMLElement;
    expect(leiItem).toBeTruthy();

    expect(leiItem.classList.contains("focused")).toBe(false);
    expect(leiItem).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(leiItem);

    expect(leiItem.classList.contains("focused")).toBe(true);
    expect(leiItem).toHaveAttribute("aria-pressed", "true");
    expect(spkItem.classList.contains("dimmed")).toBe(true);

    await userEvent.click(leiItem);
    expect(leiItem.classList.contains("focused")).toBe(false);
    expect(spkItem.classList.contains("dimmed")).toBe(false);
  });

  it("renders an error state when the projection request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).startsWith("/api/speakers/projection"))
          return new Response("boom", { status: 500 });
        return new Response("{}", { status: 200 });
      })
    );
    render(<VoiceprintMap request={REQ} />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("reports projection lifecycle through onState", async () => {
    const states: string[] = [];
    render(<VoiceprintMap request={REQ} onState={(state) => states.push(state.status)} />);

    await screen.findByRole("list", { name: /图例/ });

    expect(states).toContain("loading");
    expect(states).toContain("ready");
  });

  it("reports empty projection state through onState", async () => {
    vi.stubGlobal("fetch", mockFetch({ points: [], method: "umap", n: 0 }));
    const states: string[] = [];
    render(<VoiceprintMap request={REQ} onState={(state) => states.push(state.status)} />);

    await screen.findByText(/该范围还没有声纹/);

    expect(states).toContain("empty");
  });

  it("reports error projection state through onState", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("bad", { status: 500 })));
    const states: string[] = [];
    render(<VoiceprintMap request={REQ} onState={(state) => states.push(state.status)} />);

    await screen.findByRole("alert");

    expect(states).toContain("error");
  });

  it("re-fetches when the request prop changes", async () => {
    const { rerender } = render(<VoiceprintMap request={REQ} />);
    await screen.findByRole("list", { name: /图例/ });

    rerender(<VoiceprintMap request={{ session_ids: ["ses_2"], days: [], method: "pca" }} />);

    await waitFor(() => {
      const bodies = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
        .filter((c) => String(c[0]) === "/api/speakers/projection")
        .map((c) => JSON.parse(c[1]!.body as string));
      expect(bodies.some((b) => b.method === "pca" && b.session_ids[0] === "ses_2")).toBe(true);
    });
  });

  it("hovering does NOT reallocate the canvas backing store (no width/height churn per move)", async () => {
    const { container } = render(<VoiceprintMap request={REQ} />);
    await screen.findByRole("list", { name: /图例/ });

    const canvas = container.querySelector(".vmap-canvas") as HTMLCanvasElement;
    const w0 = canvas.width;
    const h0 = canvas.height;

    const move = (clientX: number, clientY: number) =>
      fireEvent(canvas, new MouseEvent("pointermove", { clientX, clientY, bubbles: true }));
    move(64, 336);
    move(70, 330);
    move(200, 100);

    expect(canvas.width).toBe(w0);
    expect(canvas.height).toBe(h0);
  });

  // --- color-by-session (cross-session comparison) ---
  it("toggling to 会话 colour mode renders a per-session legend", async () => {
    render(<VoiceprintMap request={{ session_ids: ["ses_1", "ses_2"], days: [], method: "umap" }} />);
    await screen.findByRole("list", { name: /图例/ });

    await userEvent.click(screen.getByRole("button", { name: /会话/ }));

    await waitFor(() => {
      const legend = screen.getByRole("list", { name: /图例/ });
      // One legend entry per distinct session id (ses_1 = 2 points, ses_2 = 2 points).
      expect(legend).toHaveTextContent("ses_1");
      expect(legend).toHaveTextContent("ses_2");
    });
    const legend = screen.getByRole("list", { name: /图例/ });
    const counts = legend.querySelectorAll(".vmap-legend-count");
    expect(Array.from(counts).map((c) => c.textContent)).toEqual(["2", "2"]);
  });

  // --- lasso-to-label (still works across the multi-scope selection) ---
  const labelPeople: PersonRow[] = [
    { person_id: "per_a", display_name: "李雷", person_type: "contact", is_self: 0, enrolled: true, attributed_count: 4, manual_count: 2 },
    { person_id: "per_b", display_name: "韩文巧", person_type: "contact", is_self: 0, enrolled: false, attributed_count: 0, manual_count: 0 }
  ];

  it("does not show the select toolbar until 框选 is toggled on (and the map still works without people)", async () => {
    render(<VoiceprintMap request={REQ} />);
    await screen.findByRole("list", { name: /图例/ });
    expect(screen.queryByRole("button", { name: /框选/ })).not.toBeInTheDocument();
  });

  it("entering select mode reveals the 标注 toolbar, disabled until a person + selection exist", async () => {
    render(<VoiceprintMap request={REQ} people={labelPeople} onLabel={vi.fn()} />);
    await screen.findByRole("list", { name: /图例/ });

    await userEvent.click(screen.getByRole("button", { name: /框选/ }));
    const label = screen.getByRole("button", { name: /^标注$/ });
    expect(label).toBeDisabled();
    expect(screen.getByText(/已选 0 点/)).toBeInTheDocument();
  });

  it("reports selected segment count when selection changes", async () => {
    const selectedCounts: number[] = [];
    render(<VoiceprintMap request={REQ} people={labelPeople} onLabel={vi.fn()} onSelectionChange={(count) => selectedCounts.push(count)} />);

    await screen.findByRole("list", { name: /图例/ });

    expect(selectedCounts).toContain(0);
  });

  it("dragging a rectangle in select mode selects the enclosed points and 标注 calls onLabel with their ids", async () => {
    const onLabel = vi.fn().mockResolvedValue({ labeled: 2 });
    const onChanged = vi.fn();
    const selectedCounts: number[] = [];
    const { container } = render(
      <VoiceprintMap
        request={REQ}
        people={labelPeople}
        onLabel={onLabel}
        onChanged={onChanged}
        onSelectionChange={(count) => selectedCounts.push(count)}
      />
    );
    await screen.findByRole("list", { name: /图例/ });
    await userEvent.click(screen.getByRole("button", { name: /框选/ }));

    const canvas = container.querySelector(".vmap-canvas") as HTMLCanvasElement;
    const pointer = (type: string, clientX: number, clientY: number) =>
      fireEvent(canvas, new MouseEvent(type, { clientX, clientY, bubbles: true }));
    pointer("pointerdown", 430, 50);
    pointer("pointermove", 600, 200);
    pointer("pointerup", 600, 200);

    expect(await screen.findByText(/已选 2 点/)).toBeInTheDocument();
    expect(selectedCounts).toContain(2);

    await userEvent.selectOptions(screen.getByLabelText(/标注为/), "per_b");
    await userEvent.click(screen.getByRole("button", { name: /^标注$/ }));

    await waitFor(() => {
      expect(onLabel).toHaveBeenCalledTimes(1);
      const [personId, ids] = onLabel.mock.calls[0];
      expect(personId).toBe("per_b");
      expect([...ids].sort()).toEqual(["seg_3", "seg_4"]);
    });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(await screen.findByText(/已选 0 点/)).toBeInTheDocument();
    expect(selectedCounts[selectedCounts.length - 1]).toBe(0);
  });

  // --- color-by-emotion ---
  it("toggling to 情绪 colour mode fetches emotion labels and switches the legend to emotion classes", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(projection, { seg_1: "开心/happy", seg_2: "开心/happy", seg_3: "难过/sad", seg_4: "中立/neutral" })
    );
    render(<VoiceprintMap request={REQ} />);
    await screen.findByRole("list", { name: /图例/ });

    expect(screen.getByText("李雷")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /情绪/ }));

    await waitFor(() => {
      const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
      const url = calls.find((u) => u.startsWith("/api/emotions/labels"));
      expect(url).toBeTruthy();
    });

    await waitFor(() => {
      const legend = screen.getByRole("list", { name: /图例/ });
      expect(legend).toHaveTextContent("开心");
      expect(legend).toHaveTextContent("难过");
      expect(legend).toHaveTextContent("中立");
    });
    const legend = screen.getByRole("list", { name: /图例/ });
    expect(legend).toHaveTextContent("🙂");
  });

  it("surfaces a capped note when the result was subsampled", async () => {
    vi.stubGlobal("fetch", mockFetch({ points, method: "umap", n: 4, capped: true, total_in_scope: 9000 }));
    render(<VoiceprintMap request={REQ} />);
    await screen.findByRole("list", { name: /图例/ });
    expect(screen.getByText(/已采样/)).toHaveTextContent("9000");
  });
});
