import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StageDurationsPanel } from "../features/pipeline/StageDurationsPanel";
import type { PipelineTaskMetric } from "../api/types";

const asrRow: PipelineTaskMetric = {
  task_type: "asr",
  counts: { succeeded: 3, failed_terminal: 1, pending: 2 },
  total: 6,
  success_rate: 0.75,
  duration_ms: { count: 4, avg: 450, p50: 200, p95: 1000, max: 1000 }
};

const noDataRow: PipelineTaskMetric = {
  task_type: "vad",
  counts: { succeeded: 0, failed_terminal: 0, pending: 3 },
  total: 3,
  success_rate: null,
  duration_ms: null
};

describe("StageDurationsPanel (阶段耗时)", () => {
  it("renders a row per task_type: 中文阶段名、成功率、P50/P95/均耗时、计数徽标", () => {
    render(<StageDurationsPanel metrics={[asrRow]} />);
    expect(screen.getByText("转写")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText(/P50 200ms/)).toBeInTheDocument();
    expect(screen.getByText(/P95 1s/)).toBeInTheDocument();
    expect(screen.getByText(/均 450ms/)).toBeInTheDocument();
    expect(screen.getByText("成功 3")).toBeInTheDocument();
    expect(screen.getByText("失败 1")).toBeInTheDocument();
    expect(screen.getByText("待处理 2")).toBeInTheDocument();
  });

  it("falls back to em-dash placeholders when duration_ms/success_rate are null", () => {
    render(<StageDurationsPanel metrics={[noDataRow]} />);
    expect(screen.getByText("预处理")).toBeInTheDocument(); // vad → 预处理 (lib/format taskTypeZh)
    expect(screen.getByText("—")).toBeInTheDocument(); // success rate placeholder
    expect(screen.getByText(/P50 —/)).toBeInTheDocument();
    expect(screen.getByText(/P95 —/)).toBeInTheDocument();
    expect(screen.getByText(/均 —/)).toBeInTheDocument();
  });

  it("renders empty-state copy when there is no task data at all", () => {
    render(<StageDurationsPanel metrics={[]} />);
    expect(screen.getByText(/暂无任务数据/)).toBeInTheDocument();
  });

  it("treats null/undefined metrics the same as an empty list", () => {
    render(<StageDurationsPanel metrics={null} />);
    expect(screen.getByText(/暂无任务数据/)).toBeInTheDocument();
  });
});
