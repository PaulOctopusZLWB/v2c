import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunInspector } from "../components/RunInspector";

describe("RunInspector", () => {
  it("disables Run while the worker is running and enables Stop", () => {
    render(<RunInspector workerRunning={true} taskCount={3} onRun={() => undefined} onStop={() => undefined} />);
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
  });
});
