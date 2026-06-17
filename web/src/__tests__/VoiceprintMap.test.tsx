import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceprintMap } from "../features/viz/VoiceprintMap";
import type { PersonRow, ProjectionPoint, ProjectionResult } from "../api/types";

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

  // --- lasso-to-label (slice 5b) ---
  const labelPeople: PersonRow[] = [
    { person_id: "per_a", display_name: "李雷", is_self: 0, enrolled: true, attributed_count: 4 },
    { person_id: "per_b", display_name: "韩文巧", is_self: 0, enrolled: false, attributed_count: 0 }
  ];

  it("does not show the select toolbar until 框选 is toggled on (and the map still works without people)", async () => {
    render(<VoiceprintMap sessionId="ses_1" day="2026-06-15" />);
    await screen.findByRole("list", { name: /图例/ });
    // No people/onLabel wired → no 框选 affordance at all.
    expect(screen.queryByRole("button", { name: /框选/ })).not.toBeInTheDocument();
  });

  it("entering select mode reveals the 标注 toolbar, disabled until a person + selection exist", async () => {
    render(<VoiceprintMap sessionId="ses_1" day="2026-06-15" people={labelPeople} onLabel={vi.fn()} />);
    await screen.findByRole("list", { name: /图例/ });

    await userEvent.click(screen.getByRole("button", { name: /框选/ }));
    // The select toolbar appears with a person picker and a disabled 标注 button (no selection yet).
    const label = screen.getByRole("button", { name: /^标注$/ });
    expect(label).toBeDisabled();
    expect(screen.getByText(/已选 0 点/)).toBeInTheDocument();
  });

  it("dragging a rectangle in select mode selects the enclosed points and 标注 calls onLabel with their ids", async () => {
    const onLabel = vi.fn().mockResolvedValue({ labeled: 2 });
    const onChanged = vi.fn();
    const { container } = render(
      <VoiceprintMap sessionId="ses_1" day="2026-06-15" people={labelPeople} onLabel={onLabel} onChanged={onChanged} />
    );
    await screen.findByRole("list", { name: /图例/ });
    await userEvent.click(screen.getByRole("button", { name: /框选/ }));

    // jsdom canvas rect is all-zero, so sizeRef falls back to 640x420 and client coords map to
    // data space as px = x*640, py = (1-y)*420. seg_3 (0.7,0.6)->px490,py168 and seg_4
    // (0.9,0.8)->px574,py84 live in the upper-right; drag a box around them.
    const canvas = container.querySelector(".vmap-canvas") as HTMLCanvasElement;
    // jsdom has no PointerEvent constructor (and ignores clientX on the generic event fireEvent
    // would build), so dispatch MouseEvent-backed pointer events — they carry clientX/clientY and
    // still trigger React's onPointer* handlers (which key off the native event type).
    const pointer = (type: string, clientX: number, clientY: number) =>
      fireEvent(canvas, new MouseEvent(type, { clientX, clientY, bubbles: true }));
    pointer("pointerdown", 430, 50);
    pointer("pointermove", 600, 200);
    pointer("pointerup", 600, 200);

    // The two enclosed points are now selected.
    expect(await screen.findByText(/已选 2 点/)).toBeInTheDocument();

    // Pick a person and commit → onLabel(personId, selectedIds).
    await userEvent.selectOptions(screen.getByLabelText(/标注为/), "per_b");
    await userEvent.click(screen.getByRole("button", { name: /^标注$/ }));

    await waitFor(() => {
      expect(onLabel).toHaveBeenCalledTimes(1);
      const [personId, ids] = onLabel.mock.calls[0];
      expect(personId).toBe("per_b");
      expect([...ids].sort()).toEqual(["seg_3", "seg_4"]);
    });
    // selection clears + onChanged fires after a successful label.
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(await screen.findByText(/已选 0 点/)).toBeInTheDocument();
  });
});
