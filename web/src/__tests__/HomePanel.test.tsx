import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HomePanel, spineStages } from "../features/home/HomePanel";
import type { HomeOverview, StatusSummary } from "../api/types";

const overview: HomeOverview = {
  review: { pending_sessions: 3, pending_segments: 47 },
  people: { total: 8, enrolled: 5 },
  coverage: { days: 12, sessions: 30, segments: 1200, embedded: 900, emoted: 600 },
  recent_sessions: [
    {
      session_id: "ses_b",
      day: "2087-05-11",
      started_at: "2087-05-11T09:00:00+08:00",
      name: "周会 · 项目排期",
      segment_count: 14,
      pending_segments: 14,
      participants: "张三 · 我",
      review_status: "pending_review"
    },
    {
      session_id: "ses_a",
      day: "2087-05-10",
      started_at: "2087-05-10T08:00:00+08:00",
      name: null,
      segment_count: 9,
      pending_segments: 0,
      participants: null,
      review_status: "accepted"
    }
  ],
  latest_day: "2087-05-11"
};

const noop = () => {};
const baseProps = {
  overview,
  error: null,
  summary: null,
  running: false,
  onStartReview: noop,
  onGoPeople: noop,
  onGoPipeline: noop,
  onOpenSession: noop
};

describe("HomePanel (今日)", () => {
  it("renders the 待审 hero (number + segments + actions), 人物 and 覆盖 cards", () => {
    render(<HomePanel {...baseProps} />);

    const reviewCard = screen.getByRole("region", { name: "待审" });
    expect(within(reviewCard).getByText("3")).toBeInTheDocument();
    expect(reviewCard.textContent).toMatch(/会话 · 47 段/);
    expect(within(reviewCard).getByRole("button", { name: /开始审核/ })).toBeInTheDocument();
    expect(within(reviewCard).getByRole("button", { name: /查看队列/ })).toBeInTheDocument();

    const peopleCard = screen.getByRole("button", { name: "人物" });
    expect(peopleCard.textContent).toMatch(/8/);
    expect(peopleCard.textContent).toMatch(/已登记 5/);

    const coverage = screen.getByRole("region", { name: "覆盖" });
    expect(within(coverage).getByText("12")).toBeInTheDocument();
    expect(within(coverage).getByText("30")).toBeInTheDocument();
    expect(within(coverage).getByText("1200")).toBeInTheDocument();
    // 声纹 900/1200=75%, 情绪 600/1200=50%
    expect(coverage.textContent).toMatch(/75%/);
    expect(coverage.textContent).toMatch(/50%/);
  });

  it("renders the recent-sessions table with 名称/参与人/状态 columns", () => {
    render(<HomePanel {...baseProps} />);
    const recent = screen.getByRole("region", { name: "最近会话" });
    const pendingRow = within(recent).getByRole("button", { name: /周会 · 项目排期/ });
    expect(pendingRow.textContent).toMatch(/张三 · 我/);
    expect(pendingRow.textContent).toMatch(/待审 14/);
    // 未命名会话回退到「会话 · N 段」;无参与人显示 —
    const acceptedRow = within(recent).getByRole("button", { name: /会话 · 9 段/ });
    expect(acceptedRow.textContent).toMatch(/已审/);
    expect(acceptedRow.textContent).toMatch(/—/);
  });

  it("clicking a recent session calls onOpenSession with its ids", async () => {
    const onOpenSession = vi.fn();
    render(<HomePanel {...baseProps} onOpenSession={onOpenSession} />);
    await userEvent.click(screen.getByRole("button", { name: /周会 · 项目排期/ }));
    expect(onOpenSession).toHaveBeenCalledWith("ses_b", "2087-05-11");
  });

  it("开始审核 / 人物卡 / 管道横条 deep-link into their tabs", async () => {
    const onStartReview = vi.fn();
    const onGoPeople = vi.fn();
    const onGoPipeline = vi.fn();
    render(<HomePanel {...baseProps} onStartReview={onStartReview} onGoPeople={onGoPeople} onGoPipeline={onGoPipeline} />);
    await userEvent.click(screen.getByRole("button", { name: /开始审核/ }));
    expect(onStartReview).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "人物" }));
    expect(onGoPeople).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "管道" }));
    expect(onGoPipeline).toHaveBeenCalled();
  });

  it("shows the all-clear state when nothing is pending", () => {
    render(<HomePanel {...baseProps} overview={{ ...overview, review: { pending_sessions: 0, pending_segments: 0 } }} />);
    expect(screen.getByText(/全部审核完毕/)).toBeInTheDocument();
  });

  it("shows the error state", () => {
    render(<HomePanel {...baseProps} overview={null} error="boom" />);
    expect(screen.getByRole("alert").textContent).toMatch(/boom/);
  });

  it("管道 spine shows idle when nothing runs, and stage states from the summary", () => {
    const { rerender } = render(<HomePanel {...baseProps} />);
    expect(screen.getByRole("button", { name: "管道" }).textContent).toMatch(/管道空闲/);

    const summary: StatusSummary = {
      status_counts: { running: 1 },
      total: 10,
      stage_counts: {
        vad: { done: 4, total: 4 },
        asr: { done: 2, total: 4 },
        session_derive: { done: 0, total: 2 }
      },
      done_total: 6,
      failed_total: 0,
      eta_seconds: 120,
      active_stage: "asr",
      current_target: "TX01",
      import_progress: null,
      worker_running: true
    };
    rerender(<HomePanel {...baseProps} summary={summary} running />);
    const spine = screen.getByRole("button", { name: "管道" });
    expect(spine.textContent).toMatch(/TX01 处理中/);
    expect(spine.textContent).toMatch(/✓ VAD/);
    expect(spine.textContent).toMatch(/转写 50%/);
  });
});

describe("spineStages", () => {
  it("maps import/summary counts to the six-stage spine", () => {
    const stages = spineStages(null, { active: true, done: 1, total: 4, current: "a.wav" });
    expect(stages[0]).toEqual({ label: "导入", state: "running", pct: 25 });
    expect(stages).toHaveLength(6);
    expect(stages.slice(1).every((s) => s.state === "pending")).toBe(true);
  });
});
