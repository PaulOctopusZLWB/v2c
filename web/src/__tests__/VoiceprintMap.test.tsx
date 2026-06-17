import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceprintMap } from "../features/viz/VoiceprintMap";
import type { ProjectionPoint, ProjectionResult } from "../api/types";

/** Four points across two person/speaker keys (per_a x2, spk_1 x2). */
const points: ProjectionPoint[] = [
  { segment_id: "seg_1", x: 0.1, y: 0.2, speaker: "spk_0", person_id: "per_a", person_label: "李雷", text: "第一段" },
  { segment_id: "seg_2", x: 0.3, y: 0.4, speaker: "spk_0", person_id: "per_a", person_label: "李雷", text: "第二段" },
  { segment_id: "seg_3", x: 0.7, y: 0.6, speaker: "spk_1", person_id: null, person_label: null, text: "第三段" },
  { segment_id: "seg_4", x: 0.9, y: 0.8, speaker: "spk_1", person_id: null, person_label: null, text: "第四段" }
];

const projection: ProjectionResult = { points, method: "umap", n: 4 };

/** A minimal 2D-context stub so canvas draw paths run without a real renderer (jsdom
 *  returns null from getContext). Every method is a no-op vi.fn(). */
function stubCanvas() {
  const ctx = new Proxy(
    {},
    {
      get: (_t, prop) => {
        if (prop === "canvas") return undefined;
        // setTransform/scale/clearRect/beginPath/arc/fill/... all become no-op fns; numeric
        // props (globalAlpha/lineWidth) read back as 0/undefined which the draw code tolerates.
        return vi.fn();
      },
      set: () => true
    }
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as unknown as CanvasRenderingContext2D);
}

/** Resolve embedding-projection with the given body; everything else is `{}`. */
function mockFetch(body: unknown = projection) {
  return vi.fn(async (url: string) => {
    const path = String(url).split("?")[0];
    if (path === "/api/speakers/embedding-projection")
      return new Response(JSON.stringify(body), { status: 200 });
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

  it("shows a loading state, then a legend with the two keys and their counts", async () => {
    render(<VoiceprintMap sessionId="ses_1" day="2026-06-15" />);

    // Loading state while the (slow) projection request is in flight.
    expect(screen.getByText(/正在投影声纹/)).toBeInTheDocument();

    // After it resolves, the legend lists both keys (person_label for labelled, speaker for not)
    // with their segment counts (2 each).
    const legend = await screen.findByRole("list", { name: /图例/ });
    expect(legend).toBeInTheDocument();
    const items = await screen.findAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(screen.getByText("李雷")).toBeInTheDocument(); // labelled key
    expect(screen.getByText("spk_1")).toBeInTheDocument(); // unlabelled falls back to speaker
    // Each cluster has 2 points.
    const counts = legend.querySelectorAll(".vmap-legend-count");
    expect(Array.from(counts).map((c) => c.textContent)).toEqual(["2", "2"]);
  });

  it("requests the projection scoped to the session/day and defaults to umap", async () => {
    render(<VoiceprintMap sessionId="ses_1" day="2026-06-15" />);
    await screen.findByRole("list", { name: /图例/ });

    const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    const projUrl = calls.find((u) => u.startsWith("/api/speakers/embedding-projection"));
    expect(projUrl).toBeTruthy();
    expect(projUrl).toContain("session_id=ses_1");
    expect(projUrl).toContain("day=2026-06-15");
    expect(projUrl).toContain("method=umap");
  });

  it("shows the empty state when the projection returns no points (n===0)", async () => {
    vi.stubGlobal("fetch", mockFetch({ points: [], method: "umap", n: 0 }));
    render(<VoiceprintMap sessionId="ses_1" day="2026-06-15" />);

    expect(await screen.findByText(/该范围还没有声纹/)).toBeInTheDocument();
    // No legend rendered for an empty projection.
    expect(screen.queryByRole("list", { name: /图例/ })).not.toBeInTheDocument();
  });

  it("clicking a legend item focuses that cluster (dims the others)", async () => {
    render(<VoiceprintMap sessionId="ses_1" day="2026-06-15" />);
    await screen.findByRole("list", { name: /图例/ });

    const leiItem = screen.getByText("李雷").closest(".vmap-legend-item") as HTMLElement;
    const spkItem = screen.getByText("spk_1").closest(".vmap-legend-item") as HTMLElement;
    expect(leiItem).toBeTruthy();

    // Initially neither is focused.
    expect(leiItem.classList.contains("focused")).toBe(false);
    expect(leiItem).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(leiItem);

    // The clicked cluster is focused; the other is dimmed.
    expect(leiItem.classList.contains("focused")).toBe(true);
    expect(leiItem).toHaveAttribute("aria-pressed", "true");
    expect(spkItem.classList.contains("dimmed")).toBe(true);

    // Clicking it again clears the focus (toggle).
    await userEvent.click(leiItem);
    expect(leiItem.classList.contains("focused")).toBe(false);
    expect(spkItem.classList.contains("dimmed")).toBe(false);
  });

  it("renders an error state when the projection request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).startsWith("/api/speakers/embedding-projection"))
          return new Response("boom", { status: 500 });
        return new Response("{}", { status: 200 });
      })
    );
    render(<VoiceprintMap sessionId="ses_1" day="2026-06-15" />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("switching the method toggle re-requests with method=pca", async () => {
    render(<VoiceprintMap sessionId="ses_1" day="2026-06-15" />);
    await screen.findByRole("list", { name: /图例/ });

    await userEvent.click(screen.getByRole("button", { name: /PCA/ }));

    await waitFor(() => {
      const calls = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
      expect(calls.some((u) => u.includes("method=pca"))).toBe(true);
    });
  });
});
