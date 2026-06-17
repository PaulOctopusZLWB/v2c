import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HomePanel } from "../features/home/HomePanel";
import type { HomeOverview } from "../api/types";

const overview: HomeOverview = {
  review: { pending_sessions: 3, pending_segments: 47 },
  people: { total: 8, enrolled: 5 },
  coverage: { days: 12, sessions: 30, segments: 1200, embedded: 900, emoted: 600 },
  recent_sessions: [
    { session_id: "ses_b", day: "2087-05-11", started_at: "2087-05-11T09:00:00+08:00", segment_count: 14, review_status: "pending_review" },
    { session_id: "ses_a", day: "2087-05-10", started_at: "2087-05-10T08:00:00+08:00", segment_count: 9, review_status: "accepted" }
  ],
  latest_day: "2087-05-11"
};

function mockFetch(body: HomeOverview = overview) {
  return vi.fn(async (url: string) => {
    if (String(url).split("?")[0] === "/api/home/overview")
      return new Response(JSON.stringify(body), { status: 200 });
    return new Response("{}", { status: 200 });
  });
}

const noop = () => {};

describe("HomePanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch());
  });

  it("renders the pending review headline, people, and coverage stats", async () => {
    render(<HomePanel onGoReview={noop} onGoSpeakers={noop} onGoLlm={noop} onOpenSession={noop} />);

    // 待审 card: the big number (pending_sessions) + "N 处待审 · M 段" sub.
    const reviewCard = await screen.findByRole("button", { name: "待审" });
    expect(within(reviewCard).getByText("3")).toBeInTheDocument();
    expect(reviewCard.textContent).toMatch(/3 处待审 · 47 段/);
    // 人物 card: total + enrolled (text is split across nodes, so assert on the card text).
    const peopleCard = screen.getByRole("button", { name: "人物" });
    expect(peopleCard.textContent).toMatch(/8/);
    expect(peopleCard.textContent).toMatch(/5 已登记声纹/);
    // 覆盖 stat strip: days / sessions / segments.
    const coverage = screen.getByRole("region", { name: "覆盖" });
    expect(within(coverage).getByText("12")).toBeInTheDocument(); // days
    expect(within(coverage).getByText("30")).toBeInTheDocument(); // sessions
    expect(within(coverage).getByText("1200")).toBeInTheDocument(); // segments
  });

  it("renders the recent-session rows", async () => {
    render(<HomePanel onGoReview={noop} onGoSpeakers={noop} onGoLlm={noop} onOpenSession={noop} />);

    const recent = await screen.findByRole("region", { name: "最近会话" });
    expect(within(recent).getByText(/2087-05-11/)).toBeInTheDocument();
    expect(within(recent).getByText(/2087-05-10/)).toBeInTheDocument();
  });

  it("clicking 待审 calls onGoReview", async () => {
    const onGoReview = vi.fn();
    render(<HomePanel onGoReview={onGoReview} onGoSpeakers={noop} onGoLlm={noop} onOpenSession={noop} />);

    await userEvent.click(await screen.findByRole("button", { name: "待审" }));
    expect(onGoReview).toHaveBeenCalledTimes(1);
  });

  it("clicking 人物 calls onGoSpeakers", async () => {
    const onGoSpeakers = vi.fn();
    render(<HomePanel onGoReview={noop} onGoSpeakers={onGoSpeakers} onGoLlm={noop} onOpenSession={noop} />);

    await userEvent.click(await screen.findByRole("button", { name: /人物/ }));
    expect(onGoSpeakers).toHaveBeenCalledTimes(1);
  });

  it("clicking 洞察 calls onGoLlm with the latest day", async () => {
    const onGoLlm = vi.fn();
    render(<HomePanel onGoReview={noop} onGoSpeakers={noop} onGoLlm={onGoLlm} onOpenSession={noop} />);

    await userEvent.click(await screen.findByRole("button", { name: /洞察/ }));
    expect(onGoLlm).toHaveBeenCalledWith("2087-05-11");
  });

  it("clicking a recent session calls onOpenSession with its ids", async () => {
    const onOpenSession = vi.fn();
    render(<HomePanel onGoReview={noop} onGoSpeakers={noop} onGoLlm={noop} onOpenSession={onOpenSession} />);

    const recent = await screen.findByRole("region", { name: "最近会话" });
    const row = within(recent).getByText(/2087-05-11/).closest("button") as HTMLButtonElement;
    await userEvent.click(row);
    expect(onOpenSession).toHaveBeenCalledWith("ses_b", "2087-05-11");
  });

  it("shows a celebratory empty state when there is nothing to review", async () => {
    vi.stubGlobal("fetch", mockFetch({ ...overview, review: { pending_sessions: 0, pending_segments: 0 } }));
    render(<HomePanel onGoReview={noop} onGoSpeakers={noop} onGoLlm={noop} onOpenSession={noop} />);

    expect(await screen.findByText("全部审核完毕")).toBeInTheDocument();
  });
});
