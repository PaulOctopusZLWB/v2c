import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ClusterListPanel } from "../features/speakers/ClusterListPanel";
import type { Person, SpeakerCluster } from "../api/types";

const persons: Person[] = [
  { person_id: "per_a", display_name: "胡春东", person_type: "contact", is_self: 0 },
  { person_id: "per_noise", display_name: "噪音/多人", person_type: "non_speaker", is_self: 0 },
];

const clusters: SpeakerCluster[] = [
  {
    speaker_cluster_id: "vp_001",
    person_id: null,
    person_label: null,
    segment_count: 2062,
    total_speech_ms: 0,
    sample_segment_id: "s1",
    sample_text: "加班公司开会",
    sample_segments: [
      { segment_id: "s1", text: "加班公司开会" },
      { segment_id: "s2", text: "今天大概想说这个方案好不好" },
      { segment_id: "s3", text: "那 MES 进行没客户吧" },
    ],
    labeled_count: 0,
  } as SpeakerCluster,
  { speaker_cluster_id: "vp_002", person_id: "per_a", person_label: "胡春东", segment_count: 800, total_speech_ms: 0, sample_segment_id: "s2", sample_text: "另一段示例", labeled_count: 700 },
];

function mockFetch(clusterList: SpeakerCluster[] = clusters) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url).split("?")[0];
    if (path === "/api/speakers/global-clusters") return new Response(JSON.stringify({ clusters: clusterList }), { status: 200 });
    if (path === "/api/persons") return new Response(JSON.stringify({ persons }), { status: 200 });
    if (path.startsWith("/api/speakers/clusters/") && init?.method === "POST")
      return new Response(JSON.stringify({ cluster_id: "vp_001", person_id: "per_a", labeled: 2062 }), { status: 200 });
    return new Response("{}", { status: 200 });
  });
}

const noop = () => {};
const confirmYes = async () => true;

describe("ClusterListPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("lists global clusters largest-first with sample, size and an assigned badge", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ClusterListPanel onChanged={noop} push={noop} confirm={confirmYes} />);

    expect(await screen.findByText("vp_001")).toBeInTheDocument();
    expect(screen.getByText("2062 段")).toBeInTheDocument();
    expect(document.querySelector(".cluster-sample")).toHaveTextContent("加班公司开会");
    expect(screen.queryByRole("combobox", { name: "分配 vp_002" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "全部" }));
    // vp_002 is already assigned -> its dropdown trigger shows that person.
    const sel2 = await screen.findByRole("combobox", { name: "分配 vp_002" });
    expect(sel2.textContent).toContain("胡春东");
  });

  it("defaults to the actionable unassigned queue and can switch back to all clusters", async () => {
    vi.stubGlobal("fetch", mockFetch());
    render(<ClusterListPanel onChanged={noop} push={noop} confirm={confirmYes} />);

    expect(await screen.findByText("vp_001")).toBeInTheDocument();
    expect(screen.queryByText("vp_002")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "未分配" })).toHaveAttribute("aria-pressed", "true");

    await userEvent.click(screen.getByRole("button", { name: "全部" }));

    expect(await screen.findByText("vp_002")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows playable sample evidence for a cluster before assigning it", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    render(<ClusterListPanel onChanged={noop} push={noop} confirm={confirmYes} />);

    await screen.findByText("vp_001");
    expect(document.querySelector(".cluster-sample")).toHaveTextContent("加班公司开会");
    const summary = screen.getByText((_, node) =>
      node?.tagName.toLowerCase() === "summary" && node.textContent?.replace(/\s+/g, " ").trim() === "样例 3 条"
    );
    expect(summary).toBeInTheDocument();
    expect((summary.closest("details") as HTMLDetailsElement).open).toBe(false);

    await userEvent.click(summary);
    expect((summary.closest("details") as HTMLDetailsElement).open).toBe(true);

    expect(screen.getByText("今天大概想说这个方案好不好")).toBeInTheDocument();
    expect(screen.getByText("那 MES 进行没客户吧")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "播放样例 2" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/audio/segments/s2"));
  });

  it("assigns a whole cluster to a person via the row dropdown", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    const onChanged = vi.fn();
    render(<ClusterListPanel onChanged={onChanged} push={noop} confirm={confirmYes} />);

    // Open the portalled Select and pick the person.
    const trigger = await screen.findByRole("combobox", { name: "分配 vp_001" });
    await userEvent.click(trigger);
    await userEvent.click(await screen.findByRole("option", { name: "胡春东" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/speakers/clusters/vp_001/assign-person"),
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("shows an empty state when there are no clusters", async () => {
    vi.stubGlobal("fetch", mockFetch([]));
    render(<ClusterListPanel onChanged={noop} push={noop} confirm={confirmYes} />);
    expect(await screen.findByText(/还没有声纹分组/)).toBeInTheDocument();
  });
});
