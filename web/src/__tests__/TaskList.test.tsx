import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TaskList } from "../components/TaskList";

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
    await userEvent.click(screen.getByRole("button", { name: /重试/ }));
    expect(onRetry).toHaveBeenCalledWith("task_1");
  });
});
