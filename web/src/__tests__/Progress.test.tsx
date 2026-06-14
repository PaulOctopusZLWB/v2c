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
});
