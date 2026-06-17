import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EmotionCharts } from "../features/viz/EmotionCharts";
import type { EmotionDistribution } from "../api/types";

// recharts pulls in ResizeObserver + SVG measurement jsdom lacks; mock to passthrough divs so the
// donut renders deterministically. The textual legend + per-speaker chips render without recharts.
vi.mock("recharts", () => {
  const Passthrough = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  return {
    ResponsiveContainer: Passthrough,
    PieChart: Passthrough,
    Pie: Passthrough,
    Cell: () => null,
    Tooltip: () => null
  };
});

const distribution: EmotionDistribution = {
  overall: { "开心/happy": 3, "难过/sad": 1, "中立/neutral": 1 },
  per_speaker: [
    { label: "李雷", total: 3, emotions: { "开心/happy": 2, "难过/sad": 1 }, dominant: "开心/happy" },
    { label: "韩梅", total: 2, emotions: { "中立/neutral": 1, "开心/happy": 1 }, dominant: "中立/neutral" }
  ],
  n: 5
};

function mockFetch(body: unknown = distribution, status = 200) {
  return vi.fn(async (url: string) => {
    if (String(url).includes("/api/emotions/distribution"))
      return new Response(JSON.stringify(body), { status });
    return new Response("{}", { status: 200 });
  });
}

describe("EmotionCharts", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders nothing and does not fetch when no session is selected", () => {
    render(<EmotionCharts sessionId={null} />);
    expect(screen.queryByText(/情绪分布/)).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("fetches and renders the overall distribution with emoji short labels and counts", async () => {
    render(<EmotionCharts sessionId="ses_e" />);

    const legend = await screen.findByRole("list", { name: /情绪分布图例/ });
    // The three classes show their zh short label + emoji + count.
    expect(legend).toHaveTextContent("开心");
    expect(legend).toHaveTextContent("🙂");
    expect(legend).toHaveTextContent("难过");
    expect(legend).toHaveTextContent("😢");
    // Counts: happy 3, sad 1, neutral 1.
    expect(legend).toHaveTextContent("3");
  });

  it("renders each speaker's emotion mix with their dominant emotion", async () => {
    render(<EmotionCharts sessionId="ses_e" />);
    await screen.findByRole("list", { name: /情绪分布图例/ });

    const speakers = screen.getByRole("list", { name: /各发言人情绪/ });
    expect(speakers).toHaveTextContent("李雷");
    expect(speakers).toHaveTextContent("韩梅");
    // 李雷's dominant is happy (🙂), 韩梅's is neutral (😐).
    const leiRow = screen.getByText("李雷").closest(".emo-speaker-row") as HTMLElement;
    expect(leiRow).toHaveTextContent("🙂");
  });

  it("shows the extract prompt when no emotions are present (n===0)", async () => {
    vi.stubGlobal("fetch", mockFetch({ overall: {}, per_speaker: [], n: 0 }));
    render(<EmotionCharts sessionId="ses_e" />);
    expect(await screen.findByText(/未提取情绪/)).toBeInTheDocument();
    // No legend when empty.
    expect(screen.queryByRole("list", { name: /情绪分布图例/ })).not.toBeInTheDocument();
  });

  it("renders an error state when the distribution request fails", async () => {
    vi.stubGlobal("fetch", mockFetch("boom", 500));
    render(<EmotionCharts sessionId="ses_e" />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
