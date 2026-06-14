import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunInspector } from "../components/RunInspector";

describe("RunInspector", () => {
  it("shows Chinese running state, gate mode, and enabled Stop", () => {
    render(<RunInspector workerRunning={true} taskCount={3} gateOn={false} onRun={() => undefined} onStop={() => undefined} />);
    expect(screen.getByText("运行中")).toBeInTheDocument();
    expect(screen.getByText("消费全部转写")).toBeInTheDocument(); // gate off badge
    expect(screen.getByRole("button", { name: "开始" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "停止" })).toBeEnabled();
  });
});
