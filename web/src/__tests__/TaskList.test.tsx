import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TaskList } from "../components/TaskList";
import type { TaskRow } from "../api/types";

function makeTasks(n: number, failedEvery = 0): TaskRow[] {
  return Array.from({ length: n }, (_, i) => ({
    task_id: `task_${i}`,
    task_type: "asr",
    target_type: "audio_chunk",
    target_id: `chk_${i}`,
    status: failedEvery && i % failedEvery === 0 ? "failed_retryable" : "succeeded",
    attempt_count: 1,
    last_error: failedEvery && i % failedEvery === 0 ? "model busy" : null,
    duration_ms: 1200
  }));
}

describe("TaskList", () => {
  it("shows failed task diagnostics and retries", async () => {
    const onRetry = vi.fn();
    render(
      <TaskList
        tasks={[{
          task_id: "task_1",
          task_type: "asr",
          target_type: "audio_chunk",
          target_id: "chk_1",
          status: "failed_retryable",
          attempt_count: 2,
          last_error: "model busy",
          duration_ms: 1200
        }]}
        onRetry={onRetry}
      />
    );

    expect(screen.getByText("model busy")).toBeInTheDocument();
    expect(screen.getByText(/2/)).toBeInTheDocument();
    expect(screen.getByText("chk_1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /重试$/ }));
    expect(onRetry).toHaveBeenCalledWith("task_1");
  });

  it("virtualizes: renders only a bounded window of rows for 500 tasks", () => {
    const { container } = render(<TaskList tasks={makeTasks(500)} onRetry={vi.fn()} />);
    const rows = container.querySelectorAll(".task-row");
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThanOrEqual(60);
  });

  it("filters to failed-only via 仅看失败", async () => {
    // 100 tasks, every 10th failed -> 10 failed.
    render(<TaskList tasks={makeTasks(100, 10)} onRetry={vi.fn()} />);
    const filter = screen.getByRole("checkbox", { name: /仅看失败/ });
    fireEvent.click(filter);
    // After filtering, every visible row must be a failed one (has a 重试 button / error).
    expect(screen.getAllByText("model busy").length).toBeGreaterThan(0);
    // A succeeded target (chk_1, not a multiple of 10) must not be visible.
    expect(screen.queryByText("chk_1")).not.toBeInTheDocument();
  });

  it("shows 重试全部失败 (N) and calls onRetryAllFailed", async () => {
    const onRetryAllFailed = vi.fn();
    render(
      <TaskList
        tasks={makeTasks(30, 10)}
        failedCount={3}
        onRetry={vi.fn()}
        onRetryAllFailed={onRetryAllFailed}
      />
    );
    const btn = screen.getByRole("button", { name: /重试全部失败/ });
    expect(btn).toHaveTextContent("3");
    await userEvent.click(btn);
    expect(onRetryAllFailed).toHaveBeenCalled();
  });
});
