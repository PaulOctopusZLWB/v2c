import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DynamicsCharts } from "../features/viz/DynamicsCharts";
import type { SessionDynamics } from "../api/types";

// recharts pulls in ResizeObserver + SVG measurement that jsdom does not implement; mock the
// pieces we use to simple divs so the donut/bar render deterministically without measuring.
vi.mock("recharts", () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    ResponsiveContainer: Passthrough,
    PieChart: Passthrough,
    Pie: Passthrough,
    Cell: () => null,
    BarChart: Passthrough,
    Bar: Passthrough,
    XAxis: () => null,
    YAxis: () => null,
    Tooltip: () => null,
    Legend: () => null
  };
});

const dynamics: SessionDynamics = {
  session_id: "ses_d",
  total_ms: 600000, // 10 minutes
  speakers: [
    { label: "李雷", talk_ms: 400000, talk_share: 0.667, turns: 4, segment_count: 8, avg_segment_ms: 50000 },
    { label: "韩梅", talk_ms: 200000, talk_share: 0.333, turns: 3, segment_count: 4, avg_segment_ms: 50000 }
  ],
  transitions: [
    { from: "李雷", to: "韩梅", count: 3 },
    { from: "韩梅", to: "李雷", count: 2 }
  ],
  timeline: [
    { label: "李雷", start_ms_rel: 0, end_ms_rel: 120000, segment_ids: ["s1", "s2"] },
    { label: "韩梅", start_ms_rel: 120000, end_ms_rel: 240000, segment_ids: ["s3"] },
    { label: "李雷", start_ms_rel: 240000, end_ms_rel: 600000, segment_ids: ["s4"] }
  ]
};

function mockFetch(body: unknown = dynamics, status = 200) {
  return vi.fn(async (url: string) => {
    if (String(url).includes("/dynamics")) return new Response(JSON.stringify(body), { status });
    return new Response("{}", { status: 200 });
  });
}

describe("DynamicsCharts", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders nothing actionable (an empty prompt) when no session is selected", () => {
    render(<DynamicsCharts sessionId={null} />);
    expect(screen.queryByText("李雷")).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("fetches and renders speaker labels with their talk-share percentages", async () => {
    render(<DynamicsCharts sessionId="ses_d" />);

    // After the fetch resolves the donut legend lists both speakers with their share %.
    const legend = await screen.findByRole("list", { name: /发言占比图例/ });
    expect(legend).toHaveTextContent("李雷");
    expect(legend).toHaveTextContent("韩梅");
    // talk_share 0.667 -> 67%, 0.333 -> 33%.
    expect(screen.getByText("67%")).toBeInTheDocument();
    expect(screen.getByText("33%")).toBeInTheDocument();
    // center label "N人 · M分钟": 2 speakers, 10 minutes.
    expect(screen.getByText(/2人/)).toBeInTheDocument();
    expect(screen.getByText(/10\s*分钟/)).toBeInTheDocument();
  });

  it("renders the custom timeline lanes (one per speaker) with positioned blocks", async () => {
    const { container } = render(<DynamicsCharts sessionId="ses_d" />);
    await screen.findByRole("list", { name: /发言占比图例/ });

    // One lane per distinct speaker (2).
    const lanes = container.querySelectorAll(".dyn-lane");
    expect(lanes).toHaveLength(2);
    // Three timeline blocks total (two 李雷 turns + one 韩梅 turn).
    const blocks = container.querySelectorAll(".dyn-block");
    expect(blocks).toHaveLength(3);
    // A block is positioned by start/width as a percentage of total_ms.
    const first = blocks[0] as HTMLElement;
    expect(first.style.left).toBe("0%");
    expect(first.style.width).toBe("20%"); // 120000/600000
  });

  it("renders the top turn-taking transitions as a styled list", async () => {
    render(<DynamicsCharts sessionId="ses_d" />);
    const turnTaking = await screen.findByRole("list", { name: /话轮接力/ });

    // The top transition "李雷 → 韩梅 ×3" appears (from -> to with its count).
    const first = turnTaking.querySelector(".dyn-transition") as HTMLElement;
    expect(first).toHaveTextContent("李雷");
    expect(first).toHaveTextContent("韩梅");
    expect(first).toHaveTextContent("×3");
  });

  it("shows the empty state when the session has no speakers", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch({ session_id: "ses_d", total_ms: 0, speakers: [], transitions: [], timeline: [] })
    );
    render(<DynamicsCharts sessionId="ses_d" />);
    expect(await screen.findByText(/还没有对话动态/)).toBeInTheDocument();
  });

  it("renders an error state when the dynamics request fails", async () => {
    vi.stubGlobal("fetch", mockFetch("boom", 500));
    render(<DynamicsCharts sessionId="ses_d" />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
