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

  it("virtualization window follows the scroll offset and preserves scrollbar geometry", () => {
    // The <=60-row cap test stays at scrollTop=0, so the offset math, the start clamp, and the
    // padTop/padBottom spacer geometry are all unobserved. Scroll to the bottom and assert the
    // window shifted to the last rows, the top is unmounted, and the top spacer grew — catching a
    // regression that ignores scrollTop, breaks the start clamp, or drops the spacers.
    const ROW_HEIGHT = 88; // must match TaskList's constant
    const { container } = render(<TaskList tasks={makeTasks(500)} onRetry={vi.fn()} />);
    const scroller = container.querySelector(".task-rows") as HTMLDivElement;

    // At rest the window starts at the top.
    expect(screen.getByText("chk_0")).toBeInTheDocument();
    expect(screen.queryByText("chk_499")).not.toBeInTheDocument();

    // Scroll near the bottom: first = floor(490) - 6 = 484, clamped to length-WINDOW = 476.
    fireEvent.scroll(scroller, { target: { scrollTop: ROW_HEIGHT * 490 } });

    expect(screen.getByText("chk_499")).toBeInTheDocument();   // the list bottom is reachable
    expect(screen.getByText("chk_476")).toBeInTheDocument();   // start clamped to length - WINDOW (500-24)
    expect(screen.queryByText("chk_0")).not.toBeInTheDocument(); // top rows unmounted
    expect(screen.queryByText("chk_475")).not.toBeInTheDocument(); // exactly at the clamp boundary
    // The top spacer height equals start * ROW_HEIGHT (476 * 88), preserving the full scrollbar.
    expect((scroller.firstElementChild as HTMLElement).style.height).toBe(`${ROW_HEIGHT * 476}px`);
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
