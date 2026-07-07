import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PipelinePanel } from "../features/pipeline/PipelinePanel";
import type { StatusSummary } from "../api/types";

/** 可控的 EventSource 桩:测试里手动派发命名事件。 */
class FakeEventSource {
  static last: FakeEventSource | null = null;
  listeners = new Map<string, Array<(e: { data: string }) => void>>();
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(type: string, cb: (e: { data: string }) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), cb]);
  }
  emit(type: string, payload: unknown) {
    for (const cb of this.listeners.get(type) ?? []) cb({ data: JSON.stringify(payload) });
  }
  close() {}
}

const summary: StatusSummary = {
  status_counts: { running: 1 },
  total: 10,
  stage_counts: { vad: { done: 4, total: 4 }, asr: { done: 2, total: 4 } },
  done_total: 6,
  failed_total: 0,
  eta_seconds: 60,
  active_stage: "asr",
  current_target: "TX01",
  import_progress: null,
  worker_running: true
};

const baseProps = {
  summary,
  running: true,
  onGoReview: vi.fn()
};

function emit(type: string, payload: unknown) {
  act(() => FakeEventSource.last!.emit(type, payload));
}

/** Map 版 localStorage 桩(该环境的 global localStorage 不可用)。 */
function fakeStorage() {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear()
  };
}

describe("PipelinePanel (管道控制室)", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
    vi.stubGlobal("localStorage", fakeStorage());
  });
  afterEach(() => vi.unstubAllGlobals());

  it("renders the stage stack: done ✓ counts, running card with progress, pending", () => {
    render(<PipelinePanel {...baseProps} />);
    const stages = screen.getByRole("complementary", { name: "管道阶段" });
    expect(stages.textContent).toMatch(/VAD/);
    expect(stages.textContent).toMatch(/4\/4/); // done counts
    const running = document.querySelector(".pipe-stage.is-running") as HTMLElement;
    expect(running.textContent).toMatch(/转写/);
    expect(running.textContent).toMatch(/2\/4/);
    expect(running.querySelector(".pipe-stage-fill")).toBeTruthy();
    // 未到的阶段是 dim 的 ○。
    expect(document.querySelectorAll(".pipe-stage.is-pending").length).toBeGreaterThan(0);
  });

  it("streams segment.transcribed into the live feed (newest gets the caret)", () => {
    render(<PipelinePanel {...baseProps} />);
    expect(screen.getByText(/等待新转写段/)).toBeInTheDocument();

    emit("segment.transcribed", { segment_id: "s1", session_id: null, text: "第一段文本", speaker: "spk_1", start_ms: 0, end_ms: 1000, absolute_start_at: "2087-05-10T14:23:07+08:00", confidence: 0.9 });
    emit("segment.transcribed", { segment_id: "s2", session_id: null, text: "第二段文本", speaker: "spk_2", start_ms: 1000, end_ms: 2000, absolute_start_at: "2087-05-10T14:23:09+08:00", confidence: 0.9 });

    expect(screen.getByText("第一段文本")).toBeInTheDocument();
    const rows = document.querySelectorAll(".pipe-row");
    expect(rows).toHaveLength(2);
    // 最新行带 live 光标。
    expect(rows[1].className).toMatch(/is-live/);
    expect(rows[1].querySelector(".pipe-caret")).toBeTruthy();
    expect(rows[0].querySelector(".pipe-caret")).toBeNull();
  });

  it("run.completed shows the ok bar with 立即审核 ↵ and logs to the event tail", async () => {
    const onGoReview = vi.fn();
    render(<PipelinePanel {...baseProps} onGoReview={onGoReview} />);
    emit("run.completed", { total: 10, done_total: 10, failed_total: 0 });

    expect(screen.getByText(/转写完成/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /立即审核/ }));
    expect(onGoReview).toHaveBeenCalled();
    // 事件流 tail 记录完成。
    expect(screen.getByText(/✓ 运行完成 · 10\/10/)).toBeInTheDocument();
  });

  it("task.failed and stage.changed land in the mono event tail", () => {
    render(<PipelinePanel {...baseProps} />);
    emit("stage.changed", { stage: "asr", previous: "vad", target: "TX01" });
    emit("task.failed", { task_id: "t9", task_type: "asr", target_id: "chunk_9", error: "timed out" });

    const tail = screen.getByLabelText("事件流");
    expect(tail.textContent).toMatch(/→ 转写 · TX01/);
    expect(tail.textContent).toMatch(/✕ 转写 失败 · chunk_9 · timed out/);
  });

  it("完成后自动跳转审核 toggle persists to localStorage", async () => {
    render(<PipelinePanel {...baseProps} />);
    const toggle = screen.getByRole("checkbox", { name: /完成后自动跳转审核/ });
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    expect(localStorage.getItem("pcn-auto-review")).toBe("1");
    await userEvent.click(toggle);
    expect(localStorage.getItem("pcn-auto-review")).toBe("0");
  });

  it("renders the injected slots (运行控制/设备/任务列表/进度)", () => {
    render(
      <PipelinePanel
        {...baseProps}
        progress={<div data-testid="slot-progress" />}
        runInspector={<div data-testid="slot-run" />}
        devicePanel={<div data-testid="slot-device" />}
        taskList={<div data-testid="slot-tasks" />}
      />
    );
    for (const id of ["slot-progress", "slot-run", "slot-device", "slot-tasks"]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
  });
});
