import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Progress } from "../components/Progress";

describe("Progress", () => {
  it("renders the count, label, and a bar whose width reflects the ratio", () => {
    render(<Progress done={3} total={8} label="转写" />);
    expect(screen.getByText("3/8")).toBeInTheDocument();
    expect(screen.getByText(/转写/)).toBeInTheDocument();

    const bar = screen.getByRole("progressbar");
    expect(bar).toHaveAttribute("aria-valuenow", "3");
    expect(bar).toHaveAttribute("aria-valuemax", "8");

    const fill = bar.querySelector(".progress-bar") as HTMLElement;
    // 3/8 = 37.5% -> rounds to 38%
    expect(fill.style.width).toBe("38%");
  });

  it("renders nothing when total is 0", () => {
    const { container } = render(<Progress done={0} total={0} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders per-stage counts when given a stages breakdown", () => {
    const { container } = render(
      <Progress
        done={1200}
        total={1502}
        stages={[
          { label: "转写", done: 1200, total: 1500 },
          { label: "发布", done: 0, total: 2 }
        ]}
      />
    );
    const breakdown = container.querySelector(".progress-stages") as HTMLElement;
    expect(breakdown).toBeInTheDocument();
    // Each stage shows its own label + done/total inside the breakdown row.
    expect(breakdown.textContent).toMatch(/转写/);
    expect(breakdown.textContent).toMatch(/1200\/1500/);
    expect(breakdown.textContent).toMatch(/发布/);
    expect(breakdown.textContent).toMatch(/0\/2/);
  });

  it("renders an ETA when given etaSeconds", () => {
    const { container } = render(<Progress done={2} total={10} etaSeconds={125} />);
    // 125s -> rounds up to whole minutes for a coarse estimate ("剩余约 3 分钟").
    const eta = container.querySelector(".progress-eta") as HTMLElement;
    expect(eta).toBeInTheDocument();
    expect(eta.textContent).toMatch(/剩余约/);
    expect(eta.textContent).toMatch(/3\s*分钟/);
  });
});
